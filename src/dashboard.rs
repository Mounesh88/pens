use axum::{
    Router,
    routing::get,
    extract::{ws::{WebSocket, WebSocketUpgrade, Message}, Path, State},
    response::Json,
};
use tower_http::services::ServeDir;
use tokio_postgres::Client as DbClient;
use std::sync::Arc;
use tokio::sync::Mutex;
use serde_json::{json, Value};

pub type SharedDb = Arc<Mutex<Option<Value>>>;
pub type SharedPg = Arc<Mutex<DbClient>>;

pub async fn start(data: SharedDb, pg: SharedPg) {
    let data_clone = data.clone();
    let pg_clone   = pg.clone();

    let app = Router::new()
        .route("/ws", get({
            let d = data_clone.clone();
            move |ws: WebSocketUpgrade| {
                let d2 = d.clone();
                async move { ws.on_upgrade(move |socket| handle_ws(socket, d2)) }
            }
        }))
        .route("/api/history/:organ", get(history_handler))
        .with_state(pg_clone)
        .nest_service("/", ServeDir::new("static"));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3001").await.unwrap();
    tracing::info!("Dashboard running at http://127.0.0.1:3001");
    axum::serve(listener, app).await.unwrap();
}

async fn handle_ws(mut socket: WebSocket, data: SharedDb) {
    tracing::info!("Dashboard client connected");
    loop {
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
        let payload = {
            let lock = data.lock().await;
            match lock.as_ref() {
                Some(d) => d.to_string(),
                None => continue,
            }
        };
        if socket.send(Message::Text(payload.into())).await.is_err() {
            tracing::info!("Dashboard client disconnected");
            break;
        }
    }
}

async fn history_handler(
    Path(organ): Path<String>,
    State(pg): State<SharedPg>,
) -> Json<Value> {
    let db = pg.lock().await;
    let rows = db.query(
        "SELECT timestamp, metric, value, unit, region
         FROM grid_readings
         WHERE organ = $1
         AND timestamp > NOW() - INTERVAL '24 hours'
         ORDER BY timestamp DESC
         LIMIT 200",
        &[&organ],
    ).await.unwrap_or_default();

    let data: Vec<Value> = rows.iter().map(|r| {
        let ts: chrono::DateTime<chrono::Utc> = r.get("timestamp");
        json!({
            "timestamp": ts.to_rfc3339(),
            "metric":    r.get::<_,&str>("metric"),
            "value":     r.get::<_,f64>("value"),
            "unit":      r.get::<_,&str>("unit"),
            "region":    r.get::<_,&str>("region"),
        })
    }).collect();

    Json(json!({ "organ": organ, "data": data }))
}

pub async fn update(data: &SharedDb, db: &DbClient) {
    let health = get_latest(db, "health",       "grid_score").await;
    let solar  = get_latest(db, "solar",        "irradiance").await;
    let wind   = get_latest(db, "wind",         "speed").await;
    let temp   = get_latest(db, "weather",      "temperature").await;
    let demand = get_latest(db, "electricity",  "demand").await;
    let risk   = get_latest(db, "forecast",     "risk_score").await;
    let freeze = get_latest(db, "forecast",     "freeze_risk").await;
    let heat   = get_latest(db, "forecast",     "heat_risk").await;
    let gas    = get_latest(db, "gas",          "storage_level").await;

    let alerts: i64 = db.query_one(
        "SELECT COUNT(*) FROM grid_alerts
         WHERE status='OPEN'
         AND timestamp > NOW() - INTERVAL '1 hour'",
        &[],
    ).await.map(|r| r.get(0)).unwrap_or(0);

    let payload = json!({
        "health":    health,
        "solar":     solar,
        "wind":      wind,
        "temp":      temp,
        "demand":    demand,
        "risk":      risk,
        "freeze":    freeze,
        "windRisk":  0.0,
        "heat":      heat,
        "solarRisk": 15.0,
        "gasTotal":  gas,
        "alerts":    alerts,
    });

    let mut lock = data.lock().await;
    *lock = Some(payload);
}

async fn get_latest(db: &DbClient, organ: &str, metric: &str) -> f64 {
    db.query_one(
        "SELECT value FROM grid_readings
         WHERE organ=$1 AND metric=$2
         ORDER BY timestamp DESC LIMIT 1",
        &[&organ, &metric],
    ).await
    .map(|r| r.get::<_, f64>("value"))
    .unwrap_or(0.0)
}