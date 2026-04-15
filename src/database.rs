use tokio_postgres::{Client, NoTls, Error};

pub async fn connect() -> Result<Client, Error> {
    let connection_str = "host=localhost port=5432 dbname=postgres user=postgres password=pens2026";
    
    let (client, connection) = tokio_postgres::connect(connection_str, NoTls).await?;
    
    tokio::spawn(async move {
        if let Err(e) = connection.await {
            tracing::error!("Database connection error: {}", e);
        }
    });

    tracing::info!("Database connected successfully");
    Ok(client)
}

pub async fn setup_tables(client: &Client) -> Result<(), Error> {
    // Grid readings table
    client.execute(
        "CREATE TABLE IF NOT EXISTS grid_readings (
            id          BIGSERIAL PRIMARY KEY,
            timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            organ       TEXT NOT NULL,
            metric      TEXT NOT NULL,
            value       DOUBLE PRECISION,
            unit        TEXT,
            region      TEXT
        )",
        &[],
    ).await?;

    // Alerts table
    client.execute(
        "CREATE TABLE IF NOT EXISTS grid_alerts (
            id          BIGSERIAL PRIMARY KEY,
            timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            alert_type  TEXT NOT NULL,
            organ       TEXT NOT NULL,
            metric      TEXT NOT NULL,
            value       DOUBLE PRECISION,
            status      TEXT DEFAULT 'OPEN'
        )",
        &[],
    ).await?;

    tracing::info!("Database tables ready — grid_readings + grid_alerts");
    Ok(())
}

pub async fn save_reading(
    client: &Client,
    organ: &str,
    metric: &str,
    value: f64,
    unit: &str,
    region: &str,
) -> Result<(), Error> {
    client.execute(
        "INSERT INTO grid_readings (timestamp, organ, metric, value, unit, region)
         VALUES (NOW(), $1, $2, $3, $4, $5)",
        &[&organ, &metric, &value, &unit, &region],
    ).await?;

    tracing::info!(
        "Saved to DB: {} | {} = {} {}",
        organ, metric, value, unit
    );
    Ok(())
}