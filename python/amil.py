import os
import json
import time
import hashlib
import requests
import psycopg2
import numpy as np
import pennylane as qml
from datetime import datetime, timezone
from dotenv import load_dotenv

import pathlib
BASE = pathlib.Path(__file__).parent.parent
load_dotenv(dotenv_path=BASE / '.env')

# ── DATABASE ──
def get_db():
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="postgres", user="postgres",
        password="pens2026"
    )

def setup_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS amil_candidates (
            id                BIGSERIAL PRIMARY KEY,
            timestamp         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            material_id       TEXT NOT NULL,
            formula           TEXT NOT NULL,
            energy_above_hull DOUBLE PRECISION,
            band_gap          DOUBLE PRECISION,
            density           DOUBLE PRECISION,
            vqe_score         DOUBLE PRECISION,
            grid_need         TEXT,
            status            TEXT DEFAULT 'screening',
            elements          TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS formula_vault (
            id                BIGSERIAL PRIMARY KEY,
            timestamp         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            material_id       TEXT NOT NULL,
            formula           TEXT NOT NULL,
            vqe_score         DOUBLE PRECISION,
            energy_above_hull DOUBLE PRECISION,
            band_gap          DOUBLE PRECISION,
            density           DOUBLE PRECISION,
            grid_need         TEXT,
            elements          TEXT,
            status            TEXT DEFAULT 'candidate',
            hash              TEXT
        )
    """)
    conn.commit()
    cur.close()
    print("[AMIL] Tables ready")

# ── MATERIALS PROJECT API ──
def search_materials(api_key, elements, limit=10):
    """Direct HTTP call to Materials Project API v2."""
    url = "https://api.materialsproject.org/materials/summary/"
    headers = {"X-API-KEY": api_key}
    params = {
        "elements":           ",".join(elements),
        "_limit":             limit,
        "_fields":            "material_id,formula_pretty,energy_above_hull,band_gap,density,elements",
        "_skip":              int(time.time()) % 100,
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            # Filter stable materials locally
            all_mats = data.get("data", [])
            stable = [m for m in all_mats
                      if m.get("energy_above_hull") is not None
                      and m.get("energy_above_hull") <= 0.1]
            return stable[:limit]
        else:
            print(f"[AMIL] API error: {r.status_code} — {r.text[:200]}")
            return []
    except Exception as e:
        print(f"[AMIL] Request error: {e}")
        return []

# ── VQE SCORING ──
dev = qml.device("default.qubit", wires=4)

@qml.qnode(dev)
def vqe_circuit(params):
    qml.RY(params[0], wires=0)
    qml.RY(params[1], wires=1)
    qml.RY(params[2], wires=2)
    qml.RY(params[3], wires=3)
    qml.CNOT(wires=[0, 1])
    qml.CNOT(wires=[1, 2])
    qml.CNOT(wires=[2, 3])
    qml.RZ(params[0] * params[1], wires=0)
    qml.RZ(params[1] * params[2], wires=1)
    qml.RZ(params[2] * params[3], wires=2)
    qml.RZ(params[3] * params[0], wires=3)
    return qml.expval(qml.PauliZ(0) @ qml.PauliZ(1))

def calculate_vqe_score(mat):
    try:
        eah  = float(mat.get('energy_above_hull') or 0)
        bg   = float(mat.get('band_gap') or 0)
        dens = float(mat.get('density') or 1)

        p0 = np.pi * max(0, 1 - eah)
        p1 = np.pi * min(bg / 5.0, 1.0)
        p2 = np.pi * min(dens / 10.0, 1.0)
        p3 = np.pi * (1 - min(eah / 2.0, 1.0))

        raw   = float(vqe_circuit(np.array([p0, p1, p2, p3])))
        score = (raw + 1) / 2 * 100

        if eah < 0.1:      score += 5
        if 0.5 < bg < 3.0: score += 5
        if bg == 0:        score -= 10

        return round(min(max(score, 0), 100), 2)
    except Exception as e:
        print(f"[AMIL] VQE error: {e}")
        return 50.0

# ── GRID NEED ──
def get_grid_need(conn):
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT metric, value FROM grid_readings
            WHERE organ IN ('weather','electricity','forecast')
            AND timestamp > NOW() - INTERVAL '2 hours'
            ORDER BY timestamp DESC LIMIT 20
        """)
        rows = cur.fetchall()
        cur.close()
        temp = demand = risk = None
        for metric, value in rows:
            if metric == 'temperature' and temp is None:   temp   = value
            if metric == 'demand'      and demand is None: demand = value
            if metric == 'risk_score'  and risk is None:   risk   = value
        if temp   and temp   > 35:    return "thermal stability >50°C — heat wave"
        if temp   and temp   < 5:     return "low temperature performance — cold grid"
        if demand and demand > 35000: return "high power density — peak demand"
        if risk   and risk   > 30:    return "rapid charge/discharge for frequency response"
        return "general grid storage optimisation"
    except:
        return "general grid storage optimisation"

# ── SAVE ──
def save_candidate(conn, mat, vqe_score, grid_need):
    cur  = conn.cursor()
    ts   = datetime.now(timezone.utc)
    f    = mat.get('formula_pretty', 'Unknown')
    mid  = mat.get('material_id',    'unknown')
    eah  = float(mat.get('energy_above_hull') or 0)
    bg   = float(mat.get('band_gap')          or 0)
    dens = float(mat.get('density')           or 0)
    els  = ','.join(mat.get('elements', []))

    cur.execute("""
        INSERT INTO amil_candidates
        (timestamp,material_id,formula,energy_above_hull,
         band_gap,density,vqe_score,grid_need,status,elements)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (ts, mid, f, eah, bg, dens, vqe_score, grid_need,
          'lab_candidate' if vqe_score >= 75 else 'screening', els))

    if vqe_score >= 70:
        h = hashlib.sha256(f"{mid}{f}{vqe_score}".encode()).hexdigest()[:16]
        cur.execute("""
            INSERT INTO formula_vault
            (timestamp,material_id,formula,vqe_score,energy_above_hull,
             band_gap,density,grid_need,elements,status,hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (ts, mid, f, vqe_score, eah, bg, dens, grid_need, els,
              'patent_pending' if vqe_score >= 90 else 'candidate', h))
        print(f"  ★ VAULT: {f:20} | Score: {vqe_score:5.1f} | Hash: {h}")

    conn.commit()
    cur.close()

# ── MAIN LOOP ──
def run():
    mp_key = os.getenv('MATERIALS_PROJECT_KEY')
    if not mp_key:
        print("[AMIL] ERROR: MATERIALS_PROJECT_KEY not set in .env")
        return

    print(f"[AMIL] Materials Project key loaded: {mp_key[:8]}...")
    conn = get_db()
    setup_tables(conn)

    # Search targets — rotate each cycle
    targets = [
        ["Li", "O"],
        ["Li", "S"],
        ["Na", "O"],
        ["Li", "Fe", "O"],
        ["Li", "Mn", "O"],
        ["Na", "Fe", "O"],
    ]

    cycle = 0
    while True:
        cycle += 1
        target = targets[cycle % len(targets)]
        print(f"\n[AMIL] === Cycle #{cycle} | Searching: {'-'.join(target)} ===")

        grid_need = get_grid_need(conn)
        print(f"[AMIL] Grid need: {grid_need}")

        materials = search_materials(mp_key, target, limit=10)
        print(f"[AMIL] Found {len(materials)} candidates")

        for mat in materials:
            vqe = calculate_vqe_score(mat)
            save_candidate(conn, mat, vqe, grid_need)
            tag = "★ LAB" if vqe >= 75 else "  ok "
            f   = mat.get('formula_pretty','?')
            bg  = float(mat.get('band_gap') or 0)
            eah = float(mat.get('energy_above_hull') or 0)
            print(f"  {tag} {f:20} | VQE:{vqe:5.1f} | Gap:{bg:.2f}eV | EAH:{eah:.3f}")
            time.sleep(0.2)

        print(f"[AMIL] Cycle #{cycle} done. Sleeping 10 min...")
        time.sleep(600)

if __name__ == "__main__":
    run()