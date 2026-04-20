import os
import time
import requests
import psycopg2
import pathlib
import numpy as np
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

BASE = pathlib.Path(__file__).parent.parent
load_dotenv(dotenv_path=BASE / '.env')

def get_db():
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="postgres", user="postgres",
        password="pens2026"
    )

def setup_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ml_forecasts (
            id           BIGSERIAL PRIMARY KEY,
            timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            forecast_for TIMESTAMPTZ NOT NULL,
            hours_ahead  INTEGER NOT NULL,
            metric       TEXT NOT NULL,
            predicted    DOUBLE PRECISION NOT NULL,
            confidence   DOUBLE PRECISION,
            model        TEXT DEFAULT 'ridge_regression'
        )
    """)
    conn.commit()
    cur.close()
    print("[FORECAST] Tables ready")

# ── FETCH OPEN-METEO 72-HOUR FORECAST ──
def get_weather_forecast():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=36.7&longitude=-119.7"
        "&hourly=temperature_2m,shortwave_radiation,wind_speed_10m"
        "&wind_speed_unit=ms&forecast_days=3"
    )
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        hourly = data.get('hourly', {})
        times  = hourly.get('time', [])
        temps  = hourly.get('temperature_2m', [])
        solar  = hourly.get('shortwave_radiation', [])
        wind   = hourly.get('wind_speed_10m', [])
        return times, temps, solar, wind
    except Exception as e:
        print(f"[FORECAST] Weather fetch error: {e}")
        return [], [], [], []

# ── GET HISTORICAL DATA FROM DATABASE ──
def get_historical(conn, organ, metric, hours=48):
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, value
        FROM grid_readings
        WHERE organ = %s AND metric = %s
        AND timestamp > NOW() - INTERVAL '%s hours'
        ORDER BY timestamp ASC
    """, (organ, metric, hours))
    rows = cur.fetchall()
    cur.close()
    return rows

# ── BUILD FEATURES ──
def build_features(hour_of_day, day_of_week, temp, solar, wind):
    """
    Simple feature vector for ML model.
    Features: hour, day, temp, solar, wind, sin/cos of hour
    """
    hour_sin = np.sin(2 * np.pi * hour_of_day / 24)
    hour_cos = np.cos(2 * np.pi * hour_of_day / 24)
    day_sin  = np.sin(2 * np.pi * day_of_week  / 7)
    day_cos  = np.cos(2 * np.pi * day_of_week  / 7)
    return [hour_of_day, day_of_week, temp, solar, wind,
            hour_sin, hour_cos, day_sin, day_cos]

# ── TRAIN MODEL ──
def train_model(conn, target_organ, target_metric):
    """Train a Ridge regression model on historical data."""
    rows = get_historical(conn, target_organ, target_metric, hours=72)

    if len(rows) < 10:
        print(f"[FORECAST] Not enough data for {target_organ}/{target_metric} — need 10+ rows, have {len(rows)}")
        return None, None

    X, y = [], []
    for ts, value in rows:
        hour    = ts.hour
        weekday = ts.weekday()
        # Use neutral weather features for historical training
        features = build_features(hour, weekday, 20.0, 400.0, 3.0)
        X.append(features)
        y.append(value)

    X = np.array(X)
    y = np.array(y)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = Ridge(alpha=1.0)
    model.fit(X_scaled, y)

    # Simple R² score
    y_pred = model.predict(X_scaled)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    print(f"[FORECAST] Model trained: {target_organ}/{target_metric} | R²: {r2:.3f} | Samples: {len(rows)}")
    return model, scaler

# ── GENERATE FORECASTS ──
def generate_forecasts(conn, model, scaler, target_organ, target_metric,
                       times, temps, solar, wind):
    if not model or not times:
        return

    now      = datetime.now(timezone.utc)
    saved    = 0
    cur      = conn.cursor()

    for i, t_str in enumerate(times[:72]):  # 72 hours ahead
        try:
            forecast_dt = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
            hours_ahead = int((forecast_dt - now).total_seconds() / 3600)

            if hours_ahead < 0:
                continue

            temp_val  = temps[i]  if i < len(temps)  else 20.0
            solar_val = solar[i]  if i < len(solar)  else 300.0
            wind_val  = wind[i]   if i < len(wind)   else 3.0

            features = build_features(
                forecast_dt.hour,
                forecast_dt.weekday(),
                temp_val, solar_val, wind_val
            )

            X = scaler.transform([features])
            predicted = float(model.predict(X)[0])
            predicted = max(0, predicted)

            # Confidence decreases with time ahead
            confidence = max(60.0, 95.0 - hours_ahead * 0.4)

            cur.execute("""
                INSERT INTO ml_forecasts
                (timestamp, forecast_for, hours_ahead, metric, predicted, confidence)
                VALUES (NOW(), %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (forecast_dt, hours_ahead, target_metric, predicted, confidence))

            saved += 1

        except Exception as e:
            continue

    conn.commit()
    cur.close()
    print(f"[FORECAST] Saved {saved} forecasts for {target_metric}")

# ── PRINT SUMMARY ──
def print_forecast_summary(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT metric, hours_ahead, predicted, confidence
        FROM ml_forecasts
        WHERE timestamp > NOW() - INTERVAL '1 hour'
        AND hours_ahead IN (6, 12, 24, 48, 72)
        ORDER BY metric, hours_ahead
    """)
    rows = cur.fetchall()
    cur.close()

    if rows:
        print("\n[FORECAST] 72-hour predictions:")
        print(f"  {'Metric':15} {'Hours':6} {'Predicted':12} {'Confidence':10}")
        print(f"  {'-'*45}")
        for metric, hours, pred, conf in rows:
            print(f"  {metric:15} {hours:6}h {pred:12.1f} {conf:8.1f}%")

# ── MAIN LOOP ──
def run():
    print("[FORECAST] 72-hour ML forecast engine starting...")
    print("[FORECAST] Models: Ridge regression on historical database")
    print("[FORECAST] Features: hour, weekday, temperature, solar, wind")

    conn = get_db()
    setup_tables(conn)

    cycle = 0

    while True:
        cycle += 1
        print(f"\n[FORECAST] === Cycle #{cycle} ===")

        try:
            # Get weather forecast
            times, temps, solar, wind = get_weather_forecast()
            print(f"[FORECAST] Weather forecast loaded — {len(times)} hours")

            # Train and forecast for each metric
            targets = [
                ('solar',       'irradiance'),
                ('wind',        'speed'),
                ('weather',     'temperature'),
                ('health',      'grid_score'),
            ]

            for organ, metric in targets:
                model, scaler = train_model(conn, organ, metric)
                if model:
                    generate_forecasts(
                        conn, model, scaler, organ, metric,
                        times, temps, solar, wind
                    )

            print_forecast_summary(conn)

        except Exception as e:
            print(f"[FORECAST] Error: {e}")

        print(f"\n[FORECAST] Next run in 30 minutes...")
        time.sleep(1800)

if __name__ == "__main__":
    run()