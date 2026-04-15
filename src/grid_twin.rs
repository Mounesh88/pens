use reqwest::Client;
use serde::Deserialize;
use chrono::Utc;
use tokio_postgres::Client as DbClient;

#[derive(Debug, Deserialize)]
struct EiaResponse {
    response: EiaData,
}

#[derive(Debug, Deserialize)]
struct EiaData {
    data: Vec<EiaRecord>,
}

#[derive(Debug, Deserialize)]
struct EiaRecord {
    period: String,
    value: Option<String>,
}

#[derive(Debug, Deserialize)]
struct WeatherResponse {
    current: CurrentWeather,
}

#[derive(Debug, Deserialize)]
struct CurrentWeather {
    shortwave_radiation: f64,
    wind_speed_10m: f64,
    temperature_2m: f64,
}

// ── ELECTRICITY — runs every 60 minutes ──
pub async fn run_electricity(db: &DbClient) {
    tracing::info!("--- ORGAN 1: ELECTRICITY (California) ---");
    let client = Client::new();
    let api_key = std::env::var("EIA_API_KEY").unwrap();

    let url = format!(
        "https://api.eia.gov/v2/electricity/rto/region-data/data/?api_key={}&frequency=hourly&data[0]=value&facets[respondent][]=CISO&facets[type][]=D&sort[0][column]=period&sort[0][direction]=desc&length=3",
        api_key
    );

    match client.get(&url).send().await {
        Ok(r) => match r.json::<EiaResponse>().await {
            Ok(data) => {
                for record in &data.response.data {
                    let value: f64 = record.value
                        .as_deref()
                        .unwrap_or("0")
                        .parse()
                        .unwrap_or(0.0);
                    tracing::info!("  [{}] Demand: {} MWh", record.period, value);
                    let _ = crate::database::save_reading(
                        db, "electricity", "demand", value, "MWh", "CISO"
                    ).await;
                }
            }
            Err(e) => tracing::error!("EIA parse error: {}", e),
        },
        Err(e) => tracing::error!("EIA request error: {}", e),
    }
}

// ── SOLAR & WIND — runs every 60 seconds ──
pub async fn run_weather(db: &DbClient) {
    tracing::info!("--- ORGAN 2: SOLAR & WIND (California) ---");
    let client = Client::new();

    let url = "https://api.open-meteo.com/v1/forecast?latitude=36.7&longitude=-119.7&current=shortwave_radiation,wind_speed_10m,temperature_2m&wind_speed_unit=ms";

    match client.get(url).send().await {
        Ok(r) => match r.json::<WeatherResponse>().await {
            Ok(data) => {
                let timestamp = Utc::now();
                tracing::info!("  [{}] Solar: {} W/m²", timestamp, data.current.shortwave_radiation);
                tracing::info!("  [{}] Wind:  {} m/s",  timestamp, data.current.wind_speed_10m);
                tracing::info!("  [{}] Temp:  {}°C",    timestamp, data.current.temperature_2m);

                let _ = crate::database::save_reading(
                    db, "solar", "irradiance",
                    data.current.shortwave_radiation, "W/m²", "California"
                ).await;
                let _ = crate::database::save_reading(
                    db, "wind", "speed",
                    data.current.wind_speed_10m, "m/s", "California"
                ).await;
                let _ = crate::database::save_reading(
                    db, "weather", "temperature",
                    data.current.temperature_2m, "°C", "California"
                ).await;
            }
            Err(e) => tracing::error!("Weather parse error: {}", e),
        },
        Err(e) => tracing::error!("Weather request error: {}", e),
    }
}

// ── WATER — runs every 5 minutes ──
pub async fn run_water(db: &DbClient) {
    tracing::info!("--- ORGAN 3: RIVER TEMPERATURE (Sacramento) ---");
    let client = Client::new();

    let url = "https://waterservices.usgs.gov/nwis/iv/?format=json&sites=11447650&parameterCd=00010&siteStatus=all";

    match client.get(url).send().await {
        Ok(r) => {
            let text = r.text().await.unwrap_or_default();
            if text.contains("value") {
                let timestamp = Utc::now();
                tracing::info!("  [{}] USGS water data received", timestamp);
                let _ = crate::database::save_reading(
                    db, "water", "river_data_received",
                    1.0, "bool", "Sacramento"
                ).await;
            } else {
                tracing::warn!("  USGS returned empty data");
            }
        }
        Err(e) => tracing::error!("USGS error: {}", e),
    }
}