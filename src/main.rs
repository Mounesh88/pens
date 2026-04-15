use dotenvy::dotenv;
use std::env;
use std::sync::Arc;
use tokio::sync::Mutex;
use tokio::time::{interval, Duration};

mod grid_twin;
mod database;
mod anomaly;
mod noaa;
mod forecast;
mod gas;
mod health;
mod dashboard;

#[tokio::main]
async fn main() {
    dotenv().ok();
    tracing_subscriber::fmt::init();

    let eia_key = env::var("EIA_API_KEY").expect("EIA_API_KEY not set");
    let gridstatus_key = env::var("GRIDSTATUS_API_KEY").expect("GRIDSTATUS_API_KEY not set");

    tracing::info!("PENS starting up...");
    tracing::info!("EIA key loaded: {}...", &eia_key[..8]);
    tracing::info!("GridStatus key loaded: {}...", &gridstatus_key[..8]);

    // Connect two database clients
    // One for the monitoring loop, one for the dashboard API
    let db = match database::connect().await {
        Ok(client) => client,
        Err(e) => {
            tracing::error!("Failed to connect to database: {}", e);
            return;
        }
    };

    let db_dash = match database::connect().await {
        Ok(client) => client,
        Err(e) => {
            tracing::error!("Failed to connect dashboard database: {}", e);
            return;
        }
    };

    // Setup tables
    if let Err(e) = database::setup_tables(&db).await {
        tracing::error!("Table setup error: {}", e);
        return;
    }

    // Shared state
    let detector   = anomaly::AnomalyDetector::new();
    let dash_data: dashboard::SharedDb = Arc::new(Mutex::new(None));
    let dash_pg:   dashboard::SharedPg = Arc::new(Mutex::new(db_dash));

    // Start dashboard server
    let dash_data_srv = dash_data.clone();
    let dash_pg_srv   = dash_pg.clone();
    tokio::spawn(async move {
        dashboard::start(dash_data_srv, dash_pg_srv).await;
    });

    tracing::info!("All systems ready.");
    tracing::info!("Dashboard at http://127.0.0.1:3001");

    // First run
    tracing::info!("=== PENS First Run ===");
    grid_twin::run_electricity(&db).await;
    grid_twin::run_weather(&db).await;
    grid_twin::run_water(&db).await;
    noaa::run(&db).await;
    forecast::run(&db).await;
    gas::run(&db).await;
    detector.check(&db).await;
    health::calculate(&db).await;
    dashboard::update(&dash_data, &db).await;

    // Tickers
    let mut weather_ticker  = interval(Duration::from_secs(60));
    let mut eia_ticker      = interval(Duration::from_secs(3600));
    let mut water_ticker    = interval(Duration::from_secs(300));
    let mut noaa_ticker     = interval(Duration::from_secs(600));
    let mut forecast_ticker = interval(Duration::from_secs(1800));
    let mut gas_ticker      = interval(Duration::from_secs(21600));

    weather_ticker.tick().await;
    eia_ticker.tick().await;
    water_ticker.tick().await;
    noaa_ticker.tick().await;
    forecast_ticker.tick().await;
    gas_ticker.tick().await;

    let mut cycle = 1u64;

    loop {
        tokio::select! {
            _ = weather_ticker.tick() => {
                cycle += 1;
                tracing::info!("=== PENS Cycle #{} ===", cycle);
                grid_twin::run_weather(&db).await;
                detector.check(&db).await;
                health::calculate(&db).await;
                dashboard::update(&dash_data, &db).await;
            }
            _ = eia_ticker.tick() => {
                tracing::info!("=== EIA Hourly Update ===");
                grid_twin::run_electricity(&db).await;
                detector.check(&db).await;
                health::calculate(&db).await;
                dashboard::update(&dash_data, &db).await;
            }
            _ = water_ticker.tick() => {
                tracing::info!("=== Water Update ===");
                grid_twin::run_water(&db).await;
            }
            _ = noaa_ticker.tick() => {
                tracing::info!("=== NOAA Alert Check ===");
                noaa::run(&db).await;
            }
            _ = forecast_ticker.tick() => {
                tracing::info!("=== Forecast & Risk Update ===");
                forecast::run(&db).await;
                dashboard::update(&dash_data, &db).await;
            }
            _ = gas_ticker.tick() => {
                tracing::info!("=== Gas Storage Update ===");
                gas::run(&db).await;
                dashboard::update(&dash_data, &db).await;
            }
        }
    }
}