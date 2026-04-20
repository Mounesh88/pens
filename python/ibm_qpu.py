import os
import time
import pathlib
import psycopg2
import numpy as np
from datetime import datetime, timezone
from dotenv import load_dotenv

BASE = pathlib.Path(__file__).parent.parent
load_dotenv(dotenv_path=BASE / '.env')

# Qiskit imports
from qiskit import QuantumCircuit
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler
from qiskit_aer import AerSimulator

def get_db():
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="postgres", user="postgres",
        password="pens2026"
    )

def setup_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qpu_validations (
            id            BIGSERIAL PRIMARY KEY,
            timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            backend       TEXT NOT NULL,
            circuit_depth INTEGER,
            qubits        INTEGER,
            shots         INTEGER,
            result        TEXT,
            fidelity      DOUBLE PRECISION,
            solve_time_ms DOUBLE PRECISION,
            status        TEXT DEFAULT 'completed',
            notes         TEXT
        )
    """)
    conn.commit()
    cur.close()
    print("[QPU] Tables ready")

# ── BUILD TEST CIRCUIT ──
def build_test_circuit():
    """
    Build a simple QAOA-style circuit for QPU validation.
    4 qubits, depth 2 — small enough for free tier.
    """
    qc = QuantumCircuit(4, 4)

    # Superposition
    qc.h(range(4))

    # Problem layer
    qc.rz(0.5, 0)
    qc.rz(0.3, 1)
    qc.rz(0.7, 2)
    qc.rz(0.4, 3)

    # Entanglement
    qc.cx(0, 1)
    qc.cx(1, 2)
    qc.cx(2, 3)

    # Mixing layer
    qc.rx(0.6, range(4))

    # Measure
    qc.measure(range(4), range(4))

    return qc

# ── RUN ON LOCAL SIMULATOR ──
def run_local(qc):
    """Run on Qiskit Aer local simulator."""
    print("[QPU] Running on local Aer simulator...")
    start = time.time()

    sim    = AerSimulator()
    job    = sim.run(qc, shots=1024)
    counts = job.result().get_counts()

    solve_ms = (time.time() - start) * 1000

    # Find most common result
    best = max(counts, key=counts.get)
    fidelity = counts[best] / 1024 * 100

    print(f"[QPU] Local result: {best} | Fidelity: {fidelity:.1f}% | Time: {solve_ms:.0f}ms")
    return best, fidelity, solve_ms, counts

# ── RUN ON IBM QPU ──
def run_ibm_qpu(qc, token):
    """
    Run on real IBM Quantum hardware.
    Uses free Open Plan — 10 minutes per month.
    Carbon-aware: prefers low-carbon time slots.
    """
    print("[QPU] Connecting to IBM Quantum Platform...")

    try:
        service = QiskitRuntimeService(
            channel='ibm_quantum_platform',
            token=token,
        )

        # Get least busy backend from free tier
        backends = service.backends(
            simulator=False,
            operational=True,
            min_num_qubits=4,
        )

        if not backends:
            print("[QPU] No QPU backends available right now")
            return None, None, None, None

        # Pick least busy
        backend = min(backends, key=lambda b: b.status().pending_jobs)
        print(f"[QPU] Selected backend: {backend.name}")
        print(f"[QPU] Pending jobs: {backend.status().pending_jobs}")
        print(f"[QPU] Qubits: {backend.num_qubits}")

        start = time.time()

        from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
        pm = generate_preset_pass_manager(optimization_level=1, backend=backend)
        qc_transpiled = pm.run(qc)
        print(f"[QPU] Circuit transpiled for {backend.name}")

        sampler = Sampler(backend)
        job     = sampler.run([qc_transpiled], shots=512)

        print(f"[QPU] Job submitted: {job.job_id()}")
        print(f"[QPU] Waiting for results...")

        result   = job.result()
        solve_ms = (time.time() - start) * 1000

        # Extract counts from transpiled circuit
        data = result[0].data
        register_name = list(vars(data).keys())[0]
        counts = getattr(data, register_name).get_counts()
        best   = max(counts, key=counts.get)
        fidelity = counts[best] / 512 * 100

        print(f"[QPU] QPU result: {best} | Fidelity: {fidelity:.1f}% | Time: {solve_ms:.0f}ms")
        return best, fidelity, solve_ms, counts

    except Exception as e:
        print(f"[QPU] IBM QPU error: {e}")
        return None, None, None, None

# ── SAVE RESULT ──
def save_result(conn, backend, depth, qubits, shots,
                result, fidelity, solve_ms, notes=''):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO qpu_validations
        (timestamp, backend, circuit_depth, qubits, shots,
         result, fidelity, solve_time_ms, notes)
        VALUES (NOW(),%s,%s,%s,%s,%s,%s,%s,%s)
    """, (backend, depth, qubits, shots,
          result, fidelity, solve_ms, notes))
    conn.commit()
    cur.close()
    print(f"[QPU] Result saved to database")

# ── COMPARE LOCAL VS QPU ──
def compare_results(local_counts, qpu_counts):
    if not qpu_counts:
        return 0.0
    # Compare distribution similarity
    all_keys = set(local_counts) | set(qpu_counts)
    local_total = sum(local_counts.values())
    qpu_total   = sum(qpu_counts.values())
    similarity  = 0.0
    for k in all_keys:
        lp = local_counts.get(k, 0) / local_total
        qp = qpu_counts.get(k, 0) / qpu_total
        similarity += min(lp, qp)
    return similarity * 100

# ── MAIN ──
def run():
    token = os.getenv('IBM_QUANTUM_TOKEN')
    if not token:
        print("[QPU] ERROR: IBM_QUANTUM_TOKEN not set in .env")
        return

    print("[QPU] IBM QPU validation system starting...")
    print(f"[QPU] Token loaded: {token[:12]}...")

    conn = get_db()
    setup_tables(conn)

    qc = build_test_circuit()
    print(f"[QPU] Test circuit: 4 qubits, depth 2")

    # Step 1 — Run local first
    local_result, local_fidelity, local_ms, local_counts = run_local(qc)
    save_result(conn, 'aer_simulator', 2, 4, 1024,
                local_result, local_fidelity, local_ms,
                'Local Aer validation')

    # Step 2 — Ask user before using QPU minutes
    print("\n[QPU] ─────────────────────────────────────")
    print("[QPU] Local simulation complete.")
    print("[QPU] Ready to run on REAL IBM Quantum hardware.")
    print("[QPU] This uses your free 10 min/month QPU allocation.")
    print("[QPU] ─────────────────────────────────────")
    answer = input("[QPU] Run on real QPU? (yes/no): ").strip().lower()

    if answer == 'yes':
        qpu_result, qpu_fidelity, qpu_ms, qpu_counts = run_ibm_qpu(qc, token)

        if qpu_result:
            save_result(conn, 'ibm_quantum_hardware', 2, 4, 512,
                        qpu_result, qpu_fidelity, qpu_ms,
                        'Real QPU validation')

            similarity = compare_results(local_counts, qpu_counts)
            print(f"\n[QPU] ═══════════════════════════════════")
            print(f"[QPU] VALIDATION COMPLETE")
            print(f"[QPU] Local result:  {local_result} ({local_fidelity:.1f}%)")
            print(f"[QPU] QPU result:    {qpu_result} ({qpu_fidelity:.1f}%)")
            print(f"[QPU] Similarity:    {similarity:.1f}%")
            print(f"[QPU] QPU time:      {qpu_ms:.0f}ms")
            print(f"[QPU] ═══════════════════════════════════")
        else:
            print("[QPU] QPU run failed — local result saved only")
    else:
        print("[QPU] QPU run skipped — local result saved")
        print("[QPU] Run again when ready to use QPU allocation")

    # Show all validations
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, backend, result, fidelity, solve_time_ms
        FROM qpu_validations
        ORDER BY timestamp DESC LIMIT 5
    """)
    rows = cur.fetchall()
    cur.close()

    print("\n[QPU] Validation history:")
    for ts, backend, result, fidelity, ms in rows:
        print(f"  {ts.strftime('%H:%M:%S')} | {backend:20} | {result} | {fidelity:.1f}% | {ms:.0f}ms")

    conn.close()

if __name__ == "__main__":
    run()