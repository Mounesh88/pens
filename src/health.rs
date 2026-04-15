use tokio_postgres::Client as DbClient;

pub struct GridHealth {
    pub score: f64,
    pub status: &'static str,
    pub electricity_score: f64,
    pub weather_score: f64,
    pub gas_score: f64,
    pub forecast_score: f64,
    pub alerts_score: f64,
}

pub async fn calculate(db: &DbClient) -> GridHealth {
    tracing::info!("--- GRID HEALTH SCORE ---");

    // ── ELECTRICITY SCORE ──
    // Get latest demand — if data exists and is reasonable = healthy
    let electricity_score = match db.query_one(
        "SELECT value FROM grid_readings
         WHERE organ = 'electricity' AND metric = 'demand'
         ORDER BY timestamp DESC LIMIT 1",
        &[],
    ).await {
        Ok(row) => {
            let demand: f64 = row.get("value");
            if demand > 0.0 && demand < 40000.0 {
                100.0
            } else if demand >= 40000.0 {
                // High demand — reduce score
                (100.0 - ((demand - 40000.0) / 1000.0)).max(0.0)
            } else {
                0.0
            }
        }
        Err(_) => 50.0, // No data yet — neutral
    };

    // ── WEATHER SCORE ──
    // Based on latest temperature and wind
    let weather_score = match db.query_one(
        "SELECT value FROM grid_readings
         WHERE organ = 'weather' AND metric = 'temperature'
         ORDER BY timestamp DESC LIMIT 1",
        &[],
    ).await {
        Ok(row) => {
            let temp: f64 = row.get("value");
            if temp >= 0.0 && temp <= 35.0 {
                100.0 // Normal range
            } else if temp < 0.0 {
                // Freeze risk
                (100.0 + temp * 5.0).max(0.0)
            } else {
                // Heat risk
                (100.0 - (temp - 35.0) * 3.0).max(0.0)
            }
        }
        Err(_) => 50.0,
    };

    // ── GAS SCORE ──
    // Based on US total storage vs normal
    let gas_score = match db.query_one(
        "SELECT value FROM grid_readings
         WHERE organ = 'gas' AND region = 'US Lower 48 Total'
         ORDER BY timestamp DESC LIMIT 1",
        &[],
    ).await {
        Ok(row) => {
            let storage: f64 = row.get("value");
            if storage >= 1800.0 {
                100.0
            } else if storage >= 1200.0 {
                // Warning zone
                (storage - 1200.0) / 6.0
            } else {
                0.0
            }
        }
        Err(_) => 50.0,
    };

    // ── FORECAST SCORE ──
    // Based on risk score — invert it (low risk = high health)
    let forecast_score = match db.query_one(
        "SELECT value FROM grid_readings
         WHERE organ = 'forecast' AND metric = 'risk_score'
         ORDER BY timestamp DESC LIMIT 1",
        &[],
    ).await {
        Ok(row) => {
            let risk: f64 = row.get("value");
            (100.0 - risk).max(0.0)
        }
        Err(_) => 50.0,
    };

    // ── ALERTS SCORE ──
    // Count open alerts in last hour
    let alerts_score = match db.query_one(
        "SELECT COUNT(*) as count FROM grid_alerts
         WHERE status = 'OPEN'
         AND timestamp > NOW() - INTERVAL '1 hour'",
        &[],
    ).await {
        Ok(row) => {
            let count: i64 = row.get("count");
            if count == 0 {
                100.0
            } else {
                (100.0 - count as f64 * 10.0).max(0.0)
            }
        }
        Err(_) => 50.0,
    };

    // ── TOTAL SCORE ──
    // Weighted average of all organs
    let score = (electricity_score * 0.30)
        + (weather_score    * 0.20)
        + (gas_score        * 0.20)
        + (forecast_score   * 0.20)
        + (alerts_score     * 0.10);

    let status = if score >= 80.0 {
        "HEALTHY"
    } else if score >= 60.0 {
        "MODERATE"
    } else if score >= 40.0 {
        "WARNING"
    } else {
        "CRITICAL"
    };

    tracing::info!("  Electricity : {:.1}/100", electricity_score);
    tracing::info!("  Weather     : {:.1}/100", weather_score);
    tracing::info!("  Gas storage : {:.1}/100", gas_score);
    tracing::info!("  Forecast    : {:.1}/100", forecast_score);
    tracing::info!("  Alerts      : {:.1}/100", alerts_score);
    tracing::info!("  ─────────────────────");
    tracing::info!("  GRID HEALTH : {:.1}/100 — {}", score, status);

    // Save to database
    let _ = crate::database::save_reading(
        db, "health", "grid_score",
        score, "score", "California"
    ).await;

    GridHealth {
        score,
        status,
        electricity_score,
        weather_score,
        gas_score,
        forecast_score,
        alerts_score,
    }
}