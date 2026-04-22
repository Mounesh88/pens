# SentinelRS

> An AI-native, zero-knowledge secrets management engine built entirely in Rust.

---

## What is SentinelRS?

Every software system runs on secrets. Database passwords, API keys, cloud tokens, encryption certificates — your applications cannot function without them. The problem is most teams manage these secrets terribly. They end up hardcoded in source files, sitting in `.env` files that get committed to Git, passed around in Slack messages, or living in shared spreadsheets with no audit trail and no expiry date.

SentinelRS fixes this. It is a secrets manager you run yourself — on your laptop, on a server, inside Docker, on Kubernetes, or even on a Raspberry Pi — that stores your secrets with military-grade encryption, scores each one for risk using an embedded AI engine, rotates them automatically before they become dangerous, and streams every access event to a live dashboard in your browser.

The entire system — CLI, REST API, WebSocket dashboard, AI engine, and rotation scheduler — compiles into a single ~8MB binary with zero external dependencies.

---

## Why SentinelRS exists

Most open-source secrets managers fall into one of two categories. They are either extremely simple (just encrypt a file with a password) or extremely complex (HashiCorp Vault requires a separate server process, complex configuration, and significant infrastructure overhead). Neither is right for a developer or small team who wants something that just works, runs anywhere, and is genuinely intelligent about secret health.

SentinelRS sits in between. It is simpler to deploy than Vault but smarter than every lightweight alternative. It is the only open-source secrets manager that combines zero-knowledge architecture, embedded ML risk intelligence, and automatic credential rotation in a single pure-Rust binary.

---

## What makes it unique

**Zero-knowledge architecture.** Your secrets are encrypted on the client side before they ever reach storage. The server stores only ciphertext. Even a complete database breach reveals nothing — without the master password, every stored secret is mathematically unreadable.

**AI-powered risk scoring.** Every secret gets a live Risk Score from 0 to 100. The embedded scoring engine checks entropy (how random is it?), age (how old is it?), pattern detection (does it match known weak passwords?), length (is it long enough?), and rotation history (has it ever been changed?). A score of 89/100 means your secret needs attention now. A score of 3/100 means it is in excellent shape.

**Intelligent auto-rotation.** The rotation engine wakes up every 60 seconds and checks every secret against your policy. If the risk score is too high, if the secret is expiring soon, or if it has not been rotated in 90 days — it generates a new cryptographically strong replacement, encrypts it, stores it, re-scores it, and logs the entire event. All without any human action.

**Single binary, all environments.** One executable runs the CLI, the REST API, the WebSocket dashboard, and the embedded storage. No runtime dependencies. No separate processes. Runs on macOS, Linux, Windows, ARM, and inside containers.

**Real-time audit dashboard.** Open `http://localhost:8080/dashboard` in your browser and watch every secret operation stream in live — who stored what, who accessed what, when rotation happened, and what the risk scores are. Every event is timestamped and persistent.

---

## How it works

The system is built in layers, each with a single responsibility.

The **crypto core** handles all encryption and decryption using ChaCha20-Poly1305 (the same algorithm used by Signal and TLS 1.3) with Argon2id key derivation. Your master password is never stored anywhere. It is used to derive an encryption key on demand, that key encrypts or decrypts your secret, and then the key is immediately wiped from memory using `zeroize`. The server never sees your plaintext.

The **storage layer** persists encrypted secrets using either an embedded sled database (zero setup, single file) for local development or PostgreSQL for production teams. Every stored secret carries metadata — a unique ID, namespace, creation timestamp, expiry date, and risk score — but the actual secret value is always a sealed envelope that only your master password can open.

The **risk engine** runs five scoring signals against every secret: Shannon entropy analysis, age-based scoring, pattern detection against a dictionary of known weak passwords and sequences, length scoring, and rotation history. These signals are combined with a weighted formula to produce a single 0–100 risk score with specific reasons and actionable recommendations.

The **rotation engine** runs as a background task. It polls every secret in your namespace on a configurable interval, checks each one against your rotation policy, and automatically generates a new cryptographically strong replacement for any secret that crosses a threshold. The new value is encrypted, stored, re-scored, and logged — all atomically.

The **REST API** exposes all vault operations over HTTP with JWT authentication. Your Databricks pipelines, Kafka consumers, MLflow servers, and CI/CD jobs can all fetch secrets at runtime without storing credentials anywhere in code.

The **WebSocket dashboard** maintains a persistent connection to your browser and pushes every event as it happens. No polling. No refreshing. Events appear the moment they occur.

---

## Real-world scenarios

**The leaking `.env` file.** A developer accidentally commits a `.env` file containing a database password to a public repository. Without SentinelRS, that password is exposed forever. With SentinelRS, applications fetch credentials at runtime from the vault — no secrets ever live in code or config files.

**The forgotten AWS key.** A cloud access key was created two years ago, shared via Slack, and never rotated. It has full production access and nobody knows who still uses it. SentinelRS scores it 91/100 CRITICAL, triggers automatic rotation, notifies all dependent services via webhooks, and retires the old key — without any human action required.

**The 3am security audit.** Your company is applying for SOC 2 compliance and the auditor asks for a log of every credential access in the last 90 days. Without a secrets manager, the answer is silence. With SentinelRS, you open the dashboard and every access event is there — user, timestamp, namespace, operation — a complete audit trail.

---

## Technology stack

SentinelRS is written entirely in Rust. The key crates are:

- **tokio** — async runtime powering the entire server and background tasks
- **axum** — HTTP server for the REST API, built on tokio
- **chacha20poly1305** — secret encryption (same algorithm as Signal)
- **argon2** — key derivation from master password (intentionally slow to defeat brute force)
- **zeroize** — cryptographic memory wiping so keys never linger in RAM
- **sled** — embedded key-value storage for local mode
- **sqlx + PostgreSQL** — relational storage for production mode
- **clap** — CLI argument parsing and command routing
- **tracing** — structured logging with environment-configurable levels
- **jsonwebtoken** — JWT creation and validation for API authentication
- **tokio-tungstenite** — WebSocket connections for the live dashboard

---

## Getting started

### Prerequisites

- Rust 1.75 or later (`rustup` recommended)
- Git

### Install

```bash
git clone https://github.com/Mounesh88/sentinelrs
cd sentinelrs
cargo build --release
```

### Set your master password

```bash
# Windows
set SENTINEL_MASTER_KEY=your-strong-master-password

# macOS / Linux
export SENTINEL_MASTER_KEY=your-strong-master-password
```

Your master password never leaves your machine. It is used locally to derive encryption keys. SentinelRS never stores it anywhere.

### Store your first secret

```bash
cargo run -- secret set DB_PASSWORD "postgres://user:pass@localhost/mydb"
```

Output:
```
Stored 'DB_PASSWORD' in namespace 'default'.
Risk Score : 42/100 (MEDIUM)
Tip        : Add symbols and increase length for a stronger secret
```

### Retrieve it

```bash
cargo run -- secret get DB_PASSWORD
```

### List all secrets with risk scores

```bash
cargo run -- secret list --show-risk
```

### Start the API server and dashboard

```bash
cargo run -- serve
```

Then open `http://localhost:8080/dashboard` in your browser.

---

## CLI commands

```
sentinelrs secret set   <name> [value]    Store a new secret
sentinelrs secret get   <name>            Retrieve and decrypt a secret
sentinelrs secret list  [--show-risk]     List all secrets in namespace
sentinelrs secret delete <name>           Permanently delete a secret
sentinelrs secret info  <name>            Show metadata without decrypting
sentinelrs secret rotate <name>           Manually rotate a secret now
sentinelrs vault init                     Initialize a new vault
sentinelrs vault status                   Show vault health and statistics
sentinelrs serve        [--port 8080]     Start the HTTP API + dashboard
```

Use `--namespace` on any command to work in a specific namespace:

```bash
sentinelrs --namespace production secret list
sentinelrs --namespace staging secret get DB_PASSWORD
```

---

## REST API

All endpoints except `/health` and `/api/auth/login` require a Bearer token obtained from the login endpoint.

```
POST   /api/auth/login          Get a JWT token (valid 1 hour)
POST   /api/secrets             Store a new secret
GET    /api/secrets             List all secrets (metadata only)
GET    /api/secrets/:name       Retrieve and decrypt a secret
GET    /api/secrets/:name/info  Get metadata without decrypting
DELETE /api/secrets/:name       Delete a secret permanently
GET    /health                  Basic health check
GET    /health/detailed         Full system health with metrics
GET    /metrics                 Prometheus metrics endpoint
GET    /dashboard               Live WebSocket dashboard (browser)
```

### Example — fetch a secret from a Python pipeline

```python
import requests, os

# authenticate once
token = requests.post("http://sentinelrs:8080/api/auth/login", json={
    "master_key": os.environ["SENTINEL_MASTER_KEY"],
    "namespace": "production"
}).json()["token"]

# fetch any secret at runtime
headers = {"Authorization": f"Bearer {token}"}
db_password = requests.get(
    "http://sentinelrs:8080/api/secrets/DB_PASSWORD",
    headers=headers
).json()["value"]
```

No credentials in code. No credentials in Git. Every access is logged.

---

## Risk scoring

Every secret receives a 0–100 risk score computed from five signals.

| Signal | Weight | What it checks |
|---|---|---|
| Entropy | 30% | How random and unpredictable is the value? |
| Pattern | 25% | Does it match known weak passwords or sequences? |
| Length | 20% | Is it long enough to resist brute force? |
| Age | 15% | How long has it been stored without rotation? |
| Rotation | 10% | Has it ever been changed? |

Risk levels:

| Score | Level | Meaning |
|---|---|---|
| 0–30 | LOW | Secret is strong and healthy |
| 31–69 | MEDIUM | Could be improved |
| 70–89 | HIGH | Rotation recommended soon |
| 90–100 | CRITICAL | Rotate immediately |

---

## Auto-rotation policy

The rotation engine checks every secret on a configurable interval (default: 60 seconds) and rotates those that meet any of these conditions:

- Risk score is at or above the threshold (default: 80/100)
- Secret expires within the warning window (default: 7 days)
- Secret has not been rotated in longer than the maximum age (default: 90 days)

When a secret is rotated, the engine generates a new 32-character cryptographically random value, encrypts it with the master key, stores it, re-scores it, and creates an audit log entry. The entire operation is atomic.

---

## Observability

SentinelRS exposes structured logs, a detailed health endpoint, and Prometheus-compatible metrics out of the box.

**Health check:**
```bash
curl http://localhost:8080/health/detailed
```

**Prometheus metrics:**
```bash
curl http://localhost:8080/metrics
```

Available metrics: `sentinelrs_requests_total`, `sentinelrs_secrets_stored`, `sentinelrs_secrets_accessed`, `sentinelrs_secrets_deleted`, `sentinelrs_secrets_rotated`, `sentinelrs_auth_success`, `sentinelrs_auth_failed`, `sentinelrs_uptime_seconds`.

Log verbosity is controlled via the `RUST_LOG` environment variable:

```bash
RUST_LOG=sentinelrs=debug cargo run -- serve
```

---

## Running tests

```bash
cargo test
```

The test suite covers 24 cases across crypto, storage, risk engine, and rotation — including full end-to-end round trips that prove a secret can be encrypted, stored, retrieved, and decrypted with byte-perfect accuracy.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SENTINEL_MASTER_KEY` | Yes | Master password for encryption/decryption |
| `SERVER__HOST` | No | Host to bind to (default: 127.0.0.1) |
| `SERVER__PORT` | No | Port to listen on (default: 8080) |
| `SERVER__MODE` | No | `local` or `production` (default: local) |
| `DATABASE__URL` | Production | PostgreSQL connection string |
| `JWT__SECRET` | No | JWT signing secret (default: change-me) |
| `JWT__EXPIRY_SECONDS` | No | Token validity in seconds (default: 3600) |
| `RUST_LOG` | No | Log level filter (default: sentinelrs=debug) |

---

## Project structure

```
sentinelrs/
├── src/
│   ├── main.rs           Entry point
│   ├── config.rs         Configuration loading
│   ├── errors.rs         Central error types
│   ├── crypto/           ChaCha20 + Argon2id encryption engine
│   ├── storage/          Sled + PostgreSQL storage backends
│   ├── cli/              Clap command definitions and handlers
│   ├── api/              Axum HTTP server, routes, JWT middleware
│   ├── risk/             AI risk scoring engine
│   ├── rotation/         Auto-rotation background engine
│   ├── dashboard/        WebSocket live event dashboard
│   └── observability/    Metrics, health checks, structured logging
├── Cargo.toml
├── .env.example
└── README.md
```

---

## Who built this and why

This project was built by Mounesh Rayalla, an ML/Data Engineer with experience across Databricks, Kafka, MLflow, and multi-cloud platforms. The goal was to build something that sits at the intersection of systems security, MLOps infrastructure, and AI-native tooling — three of the highest-demand domains in modern engineering.

Every ML pipeline, every data engineering job, every cloud service needs credentials to run. Managing those credentials safely and intelligently is an unsolved problem for most small teams. SentinelRS is the answer built in Rust from scratch.

The project demonstrates production-grade Rust, applied machine learning in a systems context, cryptographic engineering, real-time distributed systems design, and REST API development — all in one cohesive system that has no direct equivalent in the open-source ecosystem.

---

## License

MIT License — free to use, modify, and distribute.

---

## GitHub

[github.com/Mounesh88/sentinelrs](https://github.com/Mounesh88/sentinelrs)
