import os
import time
import json
import psycopg2
import numpy as np
from datetime import datetime, timezone
from dotenv import load_dotenv

# Qiskit imports
from qiskit import QuantumCircuit

from qiskit_aer import AerSimulator
from scipy.optimize import minimize

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
        CREATE TABLE IF NOT EXISTS hqcoe_decisions (
            id            BIGSERIAL PRIMARY KEY,
            timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            problem_type  TEXT NOT NULL,
            nodes         INTEGER,
            qaoa_depth    INTEGER,
            solve_time_ms DOUBLE PRECISION,
            opt_score     DOUBLE PRECISION,
            decision      TEXT,
            confidence    DOUBLE PRECISION,
            grid_state    TEXT,
            status        TEXT DEFAULT 'executed'
        )
    """)
    conn.commit()
    cur.close()
    print("[HQCOE] Tables ready")

# ── READ GRID STATE ──
def get_grid_state(conn):
    """Read latest grid readings to determine routing problem."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT organ, metric, value, unit
            FROM grid_readings
            WHERE timestamp > NOW() - INTERVAL '2 hours'
            ORDER BY timestamp DESC
            LIMIT 30
        """)
        rows = cur.fetchall()
        cur.close()

        state = {
            'solar':    0.0,
            'wind':     0.0,
            'demand':   0.0,
            'temp':     20.0,
            'risk':     0.0,
            'health':   100.0,
        }

        for organ, metric, value, unit in rows:
            if organ == 'solar'       and metric == 'irradiance':  state['solar']  = value
            if organ == 'wind'        and metric == 'speed':       state['wind']   = value
            if organ == 'electricity' and metric == 'demand':      state['demand'] = value
            if organ == 'weather'     and metric == 'temperature': state['temp']   = value
            if organ == 'forecast'    and metric == 'risk_score':  state['risk']   = value
            if organ == 'health'      and metric == 'grid_score':  state['health'] = value

        return state
    except Exception as e:
        print(f"[HQCOE] Grid state error: {e}")
        return {'solar':300,'wind':3,'demand':25000,'temp':20,'risk':5,'health':85}

# ── QUBO ENCODER ──
def build_qubo(grid_state):
    """
    Build QUBO matrix for power routing problem.
    Variables: [solar_dispatch, wind_dispatch, storage_charge,
                storage_discharge, demand_response, import_power]
    Goal: minimize cost while meeting demand
    """
    n = 6  # number of binary decision variables

    # Extract grid values
    solar   = grid_state['solar']
    wind    = grid_state['wind'] * 100   # scale to MW
    demand  = grid_state['demand']
    risk    = grid_state['risk']

    # Cost coefficients for each action
    # Lower cost = prefer this action
    costs = np.array([
        -solar  / 1000,   # solar dispatch   — free renewable
        -wind   / 1000,   # wind dispatch     — free renewable
        -0.3,             # storage charge    — store surplus
         0.5,             # storage discharge — use stored
         0.8,             # demand response   — expensive
         1.5,             # import power      — most expensive
    ])

    # Build QUBO matrix
    Q = np.diag(costs)

    # Penalty for not meeting demand
    # Add coupling terms
    demand_penalty = 2.0
    for i in range(n):
        for j in range(i+1, n):
            Q[i][j] = demand_penalty * 0.1

    # Higher risk = stronger preference for renewable dispatch
    if risk > 20:
        Q[0][0] -= 0.5  # stronger solar preference
        Q[1][1] -= 0.5  # stronger wind preference

    return Q, n

# ── QAOA CIRCUIT ──
def build_qaoa_circuit(Q, n, depth=2):
    """Build QAOA circuit for the QUBO problem."""
    num_qubits = n
    qc = QuantumCircuit(num_qubits)

    # Initial superposition
    qc.h(range(num_qubits))

    # QAOA layers
    for layer in range(depth):
        # Problem unitary (phase separation)
        gamma = 0.5  # will be optimized
        for i in range(num_qubits):
            qc.rz(2 * gamma * Q[i][i], i)
        for i in range(num_qubits):
            for j in range(i+1, num_qubits):
                if Q[i][j] != 0:
                    qc.rzz(2 * gamma * Q[i][j], i, j)

        # Mixing unitary
        beta = 0.5  # will be optimized
        qc.rx(2 * beta, range(num_qubits))

    qc.measure_all()
    return qc

# ── CLASSICAL OPTIMIZER ──
def optimize_qaoa(Q, n, depth=2):
    """
    Use classical optimizer (COBYLA) to find optimal QAOA parameters.
    This is the hybrid quantum-classical loop.
    """
    simulator = AerSimulator()
    best_result = {'score': float('inf'), 'params': None, 'bitstring': None}

    def objective(params):
        gamma_vals = params[:depth]
        beta_vals  = params[depth:]

        qc = QuantumCircuit(n)
        qc.h(range(n))

        for layer in range(depth):
            gamma = gamma_vals[layer]
            beta  = beta_vals[layer]

            for i in range(n):
                qc.rz(2 * gamma * Q[i][i], i)
            for i in range(n):
                for j in range(i+1, n):
                    if Q[i][j] != 0:
                        qc.rzz(2 * gamma * Q[i][j], i, j)
            qc.rx(2 * beta, range(n))

        qc.measure_all()

        job    = simulator.run(qc, shots=512)
        counts = job.result().get_counts()

        # Evaluate QUBO cost for each measurement outcome
        total_cost = 0
        total_shots = sum(counts.values())

        best_bitstring = None
        best_cost = float('inf')

        for bitstring, count in counts.items():
            x = np.array([int(b) for b in reversed(bitstring)])
            cost = float(x @ Q @ x)
            total_cost += cost * count / total_shots
            if cost < best_cost:
                best_cost = cost
                best_bitstring = bitstring
                if cost < best_result['score']:
                    best_result['score']     = cost
                    best_result['bitstring'] = best_bitstring

        return total_cost

    # Initial parameters
    x0 = np.random.uniform(0, np.pi, 2 * depth)

    # Optimize
    result = minimize(
        objective, x0,
        method='COBYLA',
        options={'maxiter': 50, 'rhobeg': 0.5}
    )

    best_result['params'] = result.x
    return best_result

# ── INTERPRET DECISION ──
def interpret_decision(bitstring, grid_state):
    """Convert QAOA bitstring to human-readable grid action."""
    actions = [
        'Dispatch solar generation',
        'Dispatch wind generation',
        'Charge storage from surplus',
        'Discharge storage to grid',
        'Activate demand response',
        'Import power from interconnect',
    ]

    if not bitstring:
        return "Hold current configuration", 70.0

    bits = [int(b) for b in reversed(bitstring)]
    selected = [actions[i] for i, b in enumerate(bits) if b == 1]

    if not selected:
        return "Hold current configuration — grid balanced", 85.0

    # Calculate confidence based on grid state
    confidence = 85.0
    if grid_state['health'] > 80: confidence += 5
    if grid_state['risk']   < 10: confidence += 5
    if grid_state['solar']  > 500: confidence += 3

    decision = " + ".join(selected)
    return decision, min(confidence, 98.0)

# ── SAVE DECISION ──
def save_decision(conn, problem_type, n, depth,
                  solve_ms, score, decision, confidence, grid_state):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO hqcoe_decisions
        (timestamp, problem_type, nodes, qaoa_depth,
         solve_time_ms, opt_score, decision, confidence, grid_state)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        datetime.now(timezone.utc),
        problem_type, n, depth,
        solve_ms, score, decision, confidence,
        json.dumps(grid_state)
    ))
    conn.commit()
    cur.close()

# ── MAIN LOOP ──
def run():
    print("[HQCOE] Hybrid Quantum-Classical Optimisation Engine starting...")
    print("[HQCOE] Backend: Qiskit Aer local simulator")

    conn = get_db()
    setup_tables(conn)

    cycle = 0

    while True:
        cycle += 1
        start = time.time()

        print(f"\n[HQCOE] === Cycle #{cycle} ===")

        # Read grid state
        grid_state = get_grid_state(conn)
        print(f"[HQCOE] Grid state: demand={grid_state['demand']:.0f}MW "
              f"solar={grid_state['solar']:.0f}W/m² "
              f"risk={grid_state['risk']:.1f} "
              f"health={grid_state['health']:.1f}")

        # Determine problem type
        if grid_state['risk'] > 30:
            problem = "HIGH_RISK_ROUTING"
            depth   = 3
        elif grid_state['demand'] > 35000:
            problem = "PEAK_DEMAND_ROUTING"
            depth   = 2
        else:
            problem = "STANDARD_ROUTING"
            depth   = 2

        print(f"[HQCOE] Problem: {problem} | QAOA depth: {depth}")

        # Build QUBO
        Q, n = build_qubo(grid_state)
        print(f"[HQCOE] QUBO matrix built — {n} variables")

        # Run QAOA
        print(f"[HQCOE] Running QAOA on Qiskit Aer...")
        result = optimize_qaoa(Q, n, depth)

        solve_ms = (time.time() - start) * 1000
        score    = abs(result['score'])

        # Interpret
        decision, confidence = interpret_decision(
            result['bitstring'], grid_state
        )

        print(f"[HQCOE] Decision: {decision}")
        print(f"[HQCOE] Confidence: {confidence:.1f}%")
        print(f"[HQCOE] Solve time: {solve_ms:.0f}ms")
        print(f"[HQCOE] Opt score: {score:.4f}")

        # Save
        save_decision(conn, problem, n, depth,
                      solve_ms, score, decision,
                      confidence, grid_state)

        print(f"[HQCOE] Decision saved to database")
        print(f"[HQCOE] Sleeping 5 minutes...")
        time.sleep(300)

if __name__ == "__main__":
    run()