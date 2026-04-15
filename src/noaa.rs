use reqwest::Client;
use serde::Deserialize;
use tokio_postgres::Client as DbClient;

#[derive(Debug, Deserialize)]
struct NoaaResponse {
    features: Vec<AlertFeature>,
}

#[derive(Debug, Deserialize)]
struct AlertFeature {
    properties: AlertProperties,
}

#[derive(Debug, Deserialize)]
struct AlertProperties {
    event: String,
    severity: String,
    headline: Option<String>,
    description: Option<String>,
    onset: Option<String>,
    expires: Option<String>,
}

pub async fn run(db: &DbClient) {
    tracing::info!("--- ORGAN 4: NOAA WEATHER ALERTS (California) ---");
    let client = Client::new();

    // California state alerts
    let url = "https://api.weather.gov/alerts/active?area=CA&status=actual";

    match client
        .get(url)
        .header("User-Agent", "PENS-GridMonitor/1.0 research@pens.dev")
        .send()
        .await
    {
        Ok(r) => match r.json::<NoaaResponse>().await {
            Ok(data) => {
                if data.features.is_empty() {
                    tracing::info!("  ✓ No active weather alerts in California");
                    let _ = crate::database::save_reading(
                        db,
                        "noaa",
                        "active_alerts",
                        0.0,
                        "count",
                        "California",
                    )
                    .await;
                } else {
                    tracing::warn!(
                        "  ⚠ {} active weather alerts in California",
                        data.features.len()
                    );

                    let _ = crate::database::save_reading(
                        db,
                        "noaa",
                        "active_alerts",
                        data.features.len() as f64,
                        "count",
                        "California",
                    )
                    .await;

                    for alert in &data.features {
                        let props = &alert.properties;
                        tracing::warn!(
                            "  ALERT: {} | Severity: {} | {}",
                            props.event,
                            props.severity,
                            props.headline.as_deref().unwrap_or("No headline")
                        );

                        // Save each alert to database
                        let _ = db.execute(
                            "INSERT INTO grid_alerts
                             (timestamp, alert_type, organ, metric, value, status)
                             VALUES (NOW(), $1, 'noaa', 'weather_alert', $2, 'OPEN')",
                            &[
                                &props.event,
                                &(data.features.len() as f64),
                            ],
                        ).await;
                    }
                }
            }
            Err(e) => tracing::error!("NOAA parse error: {}", e),
        },
        Err(e) => tracing::error!("NOAA request error: {}", e),
    }
}