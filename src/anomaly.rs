use tokio_postgres::Client as DbClient;

pub struct AnomalyDetector {
    pub high_demand_threshold: f64,
    pub low_solar_threshold: f64,
    pub high_wind_threshold: f64,
    pub high_temp_threshold: f64,
}

impl AnomalyDetector {
    pub fn new() -> Self {
        AnomalyDetector {
            high_demand_threshold: 30000.0, // MWh — California peak warning
            low_solar_threshold:   50.0,    // W/m² — unexpected solar drop
            high_wind_threshold:   15.0,    // m/s  — storm level wind
            high_temp_threshold:   40.0,    // °C   — extreme heat warning
        }
    }

    pub async fn check(&self, db: &DbClient) {
        tracing::info!("--- ANOMALY DETECTOR running ---");

        // Get latest readings from database
        let rows = db.query(
            "SELECT organ, metric, value, unit, timestamp
             FROM grid_readings
             WHERE timestamp > NOW() - INTERVAL '10 minutes'
             ORDER BY timestamp DESC",
            &[],
        ).await.unwrap_or_default();

        if rows.is_empty() {
            tracing::warn!("  No recent data found in database");
            return;
        }

        let mut found_anomaly = false;

        for row in &rows {
            let organ: &str  = row.get("organ");
            let metric: &str = row.get("metric");
            let value: f64   = row.get("value");

            match (organ, metric) {
                ("electricity", "demand") => {
                    if value > self.high_demand_threshold {
                        found_anomaly = true;
                        tracing::warn!(
                            "  ⚠ HIGH DEMAND: {} MWh exceeds threshold {} MWh",
                            value, self.high_demand_threshold
                        );
                        self.save_alert(db, "HIGH_DEMAND", organ, metric, value).await;
                    }
                }
                ("solar", "irradiance") => {
                    if value < self.low_solar_threshold {
                        found_anomaly = true;
                        tracing::warn!(
                            "  ⚠ SOLAR DROP: {} W/m² below threshold {} W/m²",
                            value, self.low_solar_threshold
                        );
                        self.save_alert(db, "SOLAR_DROP", organ, metric, value).await;
                    }
                }
                ("wind", "speed") => {
                    if value > self.high_wind_threshold {
                        found_anomaly = true;
                        tracing::warn!(
                            "  ⚠ HIGH WIND: {} m/s exceeds storm threshold {} m/s",
                            value, self.high_wind_threshold
                        );
                        self.save_alert(db, "HIGH_WIND", organ, metric, value).await;
                    }
                }
                ("weather", "temperature") => {
                    if value > self.high_temp_threshold {
                        found_anomaly = true;
                        tracing::warn!(
                            "  ⚠ EXTREME HEAT: {}°C exceeds threshold {}°C",
                            value, self.high_temp_threshold
                        );
                        self.save_alert(db, "EXTREME_HEAT", organ, metric, value).await;
                    }
                }
                _ => {}
            }
        }

        if !found_anomaly {
            tracing::info!("  ✓ All readings normal. Grid stable.");
        }
    }

    async fn save_alert(
        &self,
        db: &DbClient,
        alert_type: &str,
        organ: &str,
        metric: &str,
        value: f64,
    ) {
        let _ = db.execute(
            "INSERT INTO grid_alerts (timestamp, alert_type, organ, metric, value, status)
             VALUES (NOW(), $1, $2, $3, $4, 'OPEN')",
            &[&alert_type, &organ, &metric, &value],
        ).await;

        tracing::warn!(
            "  Alert saved to DB: {} | {} | {} = {}",
            alert_type, organ, metric, value
        );
    }
}