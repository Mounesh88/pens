use reqwest::Client;
use serde::Deserialize;
use tokio_postgres::Client as DbClient;

#[derive(Debug, Deserialize)]
struct ForecastResponse {
    hourly: HourlyData,
}

#[derive(Debug, Deserialize)]
struct HourlyData {
    time: Vec<String>,
    temperature_2m: Vec<f64>,
    wind_speed_10m: Vec<f64>,
    shortwave_radiation: Vec<f64>,
    precipitation: Vec<f64>,
}

pub struct RiskScore {
    pub total: f64,
    pub freeze_risk: f64,
    pub wind_risk: f64,
    pub heat_risk: f64,
    pub solar_drop_risk: f64,
    pub summary: String,
}

pub async fn run(db: &DbClient) {
    tracing::info!("--- ORGAN 5: FORECAST & RISK ENGINE ---");
    let client = Client::new();

    // Get 7 day hourly forecast for California
    let url = "https://api.open-meteo.com/v1/forecast?\
        latitude=36.7&longitude=-119.7\
        &hourly=temperature_2m,wind_speed_10m,shortwave_radiation,precipitation\
        &wind_speed_unit=ms\
        &forecast_days=7";

    match client.get(url).send().await {
        Ok(r) => match r.json::<ForecastResponse>().await {
            Ok(data) => {
                let risk = calculate_risk(&data);

                tracing::info!("  Risk Score: {:.1}/100", risk.total);
                tracing::info!("  Freeze risk:    {:.1}", risk.freeze_risk);
                tracing::info!("  Wind risk:      {:.1}", risk.wind_risk);
                tracing::info!("  Heat risk:      {:.1}", risk.heat_risk);
                tracing::info!("  Solar drop:     {:.1}", risk.solar_drop_risk);
                tracing::info!("  Summary: {}", risk.summary);

                // Save risk score to database
                let _ = crate::database::save_reading(
                    db, "forecast", "risk_score",
                    risk.total, "score", "California"
                ).await;
                let _ = crate::database::save_reading(
                    db, "forecast", "freeze_risk",
                    risk.freeze_risk, "score", "California"
                ).await;
                let _ = crate::database::save_reading(
                    db, "forecast", "wind_risk",
                    risk.wind_risk, "score", "California"
                ).await;
                let _ = crate::database::save_reading(
                    db, "forecast", "heat_risk",
                    risk.heat_risk, "score", "California"
                ).await;

                // Save alert if risk is high
                if risk.total > 60.0 {
                    tracing::warn!("  ⚠ HIGH RISK DETECTED: {}", risk.summary);
                    let _ = db.execute(
                        "INSERT INTO grid_alerts
                         (timestamp, alert_type, organ, metric, value, status)
                         VALUES (NOW(), 'HIGH_RISK_FORECAST', 'forecast', 'risk_score', $1, 'OPEN')",
                        &[&risk.total],
                    ).await;
                } else if risk.total > 30.0 {
                    tracing::warn!("  ⚠ MODERATE RISK: {}", risk.summary);
                } else {
                    tracing::info!("  ✓ Low risk forecast. Grid conditions favourable.");
                }
            }
            Err(e) => tracing::error!("Forecast parse error: {}", e),
        },
        Err(e) => tracing::error!("Forecast request error: {}", e),
    }
}

fn calculate_risk(data: &ForecastResponse) -> RiskScore {
    let hours = &data.hourly;
    let count = hours.time.len() as f64;

    // ── FREEZE RISK ──
    // How many hours below 2°C in next 7 days
    let freeze_hours = hours.temperature_2m.iter()
        .filter(|&&t| t < 2.0)
        .count() as f64;
    let freeze_risk = (freeze_hours / count * 100.0).min(100.0);

    // ── WIND RISK ──
    // How many hours above 12 m/s in next 7 days
    let high_wind_hours = hours.wind_speed_10m.iter()
        .filter(|&&w| w > 12.0)
        .count() as f64;
    let wind_risk = (high_wind_hours / count * 100.0).min(100.0);

    // ── HEAT RISK ──
    // How many hours above 38°C in next 7 days
    let heat_hours = hours.temperature_2m.iter()
        .filter(|&&t| t > 38.0)
        .count() as f64;
    let heat_risk = (heat_hours / count * 100.0).min(100.0);

    // ── SOLAR DROP RISK ──
    // Daytime hours with very low radiation (cloudy/stormy)
    let solar_drop_hours = hours.shortwave_radiation.iter()
        .filter(|&&s| s < 50.0)
        .count() as f64;
    let solar_drop_risk = (solar_drop_hours / count * 30.0).min(100.0);

    // ── TOTAL RISK SCORE ──
    // Weighted combination
    let total = (freeze_risk * 0.35)
        + (wind_risk   * 0.25)
        + (heat_risk   * 0.25)
        + (solar_drop_risk * 0.15);

    // ── SUMMARY ──
    let summary = if freeze_risk > 20.0 {
        format!("Freeze risk dominant — {} freeze hours forecast", freeze_hours as u32)
    } else if wind_risk > 20.0 {
        format!("High wind risk — {} storm hours forecast", high_wind_hours as u32)
    } else if heat_risk > 20.0 {
        format!("Heat wave risk — {} extreme heat hours forecast", heat_hours as u32)
    } else {
        "All conditions within normal range".to_string()
    };

    RiskScore {
        total,
        freeze_risk,
        wind_risk,
        heat_risk,
        solar_drop_risk,
        summary,
    }
}