use reqwest::Client;
use serde::Deserialize;
use std::collections::HashMap;
use tokio_postgres::Client as DbClient;

#[derive(Debug, Deserialize)]
struct EiaGasResponse {
    response: GasData,
}

#[derive(Debug, Deserialize)]
struct GasData {
    data: Vec<GasRecord>,
}

#[derive(Debug, Deserialize)]
struct GasRecord {
    period: String,
    value: Option<String>,
    #[serde(rename = "area-name")]
    area_name: Option<String>,
    duoarea: Option<String>,
}

struct RegionThreshold {
    name: String,
    warning: f64,
    critical: f64,
}

fn get_threshold(area: &str) -> RegionThreshold {
    match area {
        "R48" => RegionThreshold {
            name: "US Lower 48 Total".to_string(),
            warning: 1800.0,
            critical: 1200.0,
        },
        "R33" => RegionThreshold {
            name: "Midwest".to_string(),
            warning: 500.0,
            critical: 300.0,
        },
        "R32" => RegionThreshold {
            name: "Mountain".to_string(),
            warning: 150.0,
            critical: 80.0,
        },
        "R31" => RegionThreshold {
            name: "Pacific".to_string(),
            warning: 200.0,
            critical: 100.0,
        },
        "R34" => RegionThreshold {
            name: "South Central Salt".to_string(),
            warning: 150.0,
            critical: 80.0,
        },
        "R35" => RegionThreshold {
            name: "South Central Nonsalt".to_string(),
            warning: 200.0,
            critical: 100.0,
        },
        _ => RegionThreshold {
            name: area.to_string(),
            warning: 300.0,
            critical: 150.0,
        },
    }
}

pub async fn run(db: &DbClient) {
    tracing::info!("--- ORGAN 6: NATURAL GAS STORAGE ---");
    let client = Client::new();
    let api_key = std::env::var("EIA_API_KEY").unwrap();

    let url = format!(
        "https://api.eia.gov/v2/natural-gas/stor/wkly/data/?api_key={}&frequency=weekly&data[0]=value&sort[0][column]=period&sort[0][direction]=desc&length=10",
        api_key
    );

    match client.get(&url).send().await {
        Ok(r) => match r.json::<EiaGasResponse>().await {
            Ok(data) => {
                if data.response.data.is_empty() {
                    tracing::warn!("  No gas storage data returned");
                    return;
                }

                // Keep only the most recent record per region
                let mut latest: HashMap<String, GasRecord> = HashMap::new();
                for record in data.response.data {
                    let area = record.duoarea
                        .clone()
                        .unwrap_or("Unknown".to_string());
                    latest.entry(area).or_insert(record);
                }

                tracing::info!("  Weekly US Natural Gas Storage — latest per region:");

                for (area_code, record) in &latest {
                    let value: f64 = record.value
                        .as_deref()
                        .unwrap_or("0")
                        .replace(",", "")
                        .parse()
                        .unwrap_or(0.0);

                    let threshold = get_threshold(area_code);

                    let _ = crate::database::save_reading(
                        db,
                        "gas",
                        "storage_level",
                        value,
                        "Bcf",
                        &threshold.name,
                    ).await;

                    if value > 0.0 && value < threshold.critical {
                        tracing::warn!(
                            "  ⚠ CRITICAL: {} at {} Bcf — critical threshold {} Bcf",
                            threshold.name, value, threshold.critical
                        );
                        let _ = db.execute(
                            "INSERT INTO grid_alerts
                             (timestamp, alert_type, organ, metric, value, status)
                             VALUES (NOW(), 'CRITICAL_GAS_STORAGE', 'gas', $1, $2, 'OPEN')",
                            &[&threshold.name, &value],
                        ).await;
                    } else if value > 0.0 && value < threshold.warning {
                        tracing::warn!(
                            "  ⚠ WARNING: {} at {} Bcf — warning threshold {} Bcf",
                            threshold.name, value, threshold.warning
                        );
                    } else if value > 0.0 {
                        tracing::info!(
                            "  ✓ {} normal at {} Bcf",
                            threshold.name, value
                        );
                    }
                }
            }
            Err(e) => tracing::error!("Gas parse error: {}", e),
        },
        Err(e) => tracing::error!("Gas request error: {}", e),
    }
}