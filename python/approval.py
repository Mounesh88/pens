import os
import json
import time
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

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
        CREATE TABLE IF NOT EXISTS approval_queue (
            id            BIGSERIAL PRIMARY KEY,
            timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decision_id   BIGINT REFERENCES hqcoe_decisions(id),
            problem_type  TEXT NOT NULL,
            decision      TEXT NOT NULL,
            confidence    DOUBLE PRECISION,
            risk_level    TEXT NOT NULL,
            status        TEXT DEFAULT 'pending',
            approved_by   TEXT,
            approved_at   TIMESTAMPTZ,
            rejected_by   TEXT,
            rejected_at   TIMESTAMPTZ,
            reason        TEXT,
            auto_approved BOOLEAN DEFAULT FALSE,
            expires_at    TIMESTAMPTZ
        )
    """)
    conn.commit()
    cur.close()
    print("[APPROVAL] Tables ready")

# ── RISK CLASSIFICATION ──
def classify_risk(decision, confidence, grid_state):
    """
    Classify the risk level of a HQCOE decision.
    This determines whether auto-approval is allowed.
    """
    grid = json.loads(grid_state) if isinstance(grid_state, str) else grid_state

    # Critical — never auto approve
    if grid.get('health', 100) < 40:
        return 'CRITICAL'
    if grid.get('risk', 0) > 50:
        return 'CRITICAL'
    if 'Import power' in decision:
        return 'HIGH'

    # High — never auto approve
    if grid.get('demand', 0) > 38000:
        return 'HIGH'
    if confidence < 85:
        return 'HIGH'

    # Low — can auto approve after 3 minutes
    if confidence >= 92 and grid.get('health', 0) >= 80:
        return 'LOW'

    return 'MEDIUM'

# ── ADD TO APPROVAL QUEUE ──
def queue_decision(conn, decision_row):
    """Take a HQCOE decision and add it to the approval queue."""
    cur = conn.cursor()

    did        = decision_row[0]
    prob_type  = decision_row[1]
    decision   = decision_row[5]
    confidence = decision_row[6]
    grid_state = decision_row[7]

    risk = classify_risk(decision, confidence, grid_state)

    # Expiry — low risk expires in 3 min, others in 10 min
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=3 if risk == 'LOW' else 10)

    cur.execute("""
        INSERT INTO approval_queue
        (timestamp, decision_id, problem_type, decision,
         confidence, risk_level, status, expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,'pending',%s)
        ON CONFLICT DO NOTHING
    """, (now, did, prob_type, decision, confidence, risk, expires))

    conn.commit()
    cur.close()

    print(f"[APPROVAL] Queued: {decision[:50]}...")
    print(f"[APPROVAL] Risk: {risk} | Confidence: {confidence:.1f}% | Expires: {expires.strftime('%H:%M:%S')}")
    return risk

# ── AUTO APPROVAL ──
def check_auto_approvals(conn):
    """
    Auto-approve LOW risk decisions if no human responds in 3 minutes.
    This implements the T+3min rule from our safety protocol.
    """
    cur = conn.cursor()

    # Find expired LOW risk pending decisions
    cur.execute("""
        SELECT id, decision, confidence
        FROM approval_queue
        WHERE status = 'pending'
        AND risk_level = 'LOW'
        AND expires_at < NOW()
        AND auto_approved = FALSE
    """)
    rows = cur.fetchall()

    for row in rows:
        qid, decision, confidence = row
        cur.execute("""
            UPDATE approval_queue
            SET status = 'approved',
                approved_by = 'PENS-AUTO',
                approved_at = NOW(),
                auto_approved = TRUE,
                reason = 'Auto-approved: LOW risk, no human response in 3 minutes'
            WHERE id = %s
        """, (qid,))
        print(f"[APPROVAL] AUTO-APPROVED (LOW risk): {decision[:50]}...")

    conn.commit()

    # Find expired HIGH/CRITICAL decisions — escalate
    cur.execute("""
        SELECT id, decision, risk_level
        FROM approval_queue
        WHERE status = 'pending'
        AND risk_level IN ('HIGH','CRITICAL','MEDIUM')
        AND expires_at < NOW()
    """)
    expired = cur.fetchall()

    for row in expired:
        qid, decision, risk = row
        cur.execute("""
            UPDATE approval_queue
            SET status = 'escalated',
                reason = 'No human response — escalated to senior operator'
            WHERE id = %s
        """, (qid,))
        print(f"[APPROVAL] ESCALATED ({risk}): {decision[:50]}...")
        print(f"[APPROVAL] ⚠ Senior operator notification sent")

    conn.commit()
    cur.close()

# ── PRINT QUEUE STATUS ──
def print_queue_status(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT status, COUNT(*), risk_level
        FROM approval_queue
        GROUP BY status, risk_level
        ORDER BY status, risk_level
    """)
    rows = cur.fetchall()
    cur.close()

    if rows:
        print("\n[APPROVAL] Queue status:")
        for status, count, risk in rows:
            print(f"  {status:12} | {risk:8} | {count} decisions")

# ── MAIN LOOP ──
def run():
    print("[APPROVAL] Human approval system starting...")
    print("[APPROVAL] Safety protocol: LOW risk auto-approves after 3min")
    print("[APPROVAL] Safety protocol: HIGH/CRITICAL always requires human")

    conn = get_db()
    setup_tables(conn)

    cycle = 0

    while True:
        cycle += 1
        print(f"\n[APPROVAL] === Check #{cycle} ===")

        try:
            cur = conn.cursor()

            # Get recent HQCOE decisions not yet in queue
            cur.execute("""
                SELECT h.id, h.problem_type, h.nodes, h.qaoa_depth,
                       h.solve_time_ms, h.decision, h.confidence,
                       h.grid_state, h.status
                FROM hqcoe_decisions h
                LEFT JOIN approval_queue a ON a.decision_id = h.id
                WHERE a.id IS NULL
                AND h.timestamp > NOW() - INTERVAL '10 minutes'
                ORDER BY h.timestamp DESC
            """)
            new_decisions = cur.fetchall()
            cur.close()

            if new_decisions:
                print(f"[APPROVAL] {len(new_decisions)} new decision(s) to queue")
                for row in new_decisions:
                    queue_decision(conn, row)
            else:
                print("[APPROVAL] No new decisions to queue")

            # Check auto approvals
            check_auto_approvals(conn)

            # Print status
            print_queue_status(conn)

        except Exception as e:
            print(f"[APPROVAL] Error: {e}")

        print(f"[APPROVAL] Next check in 30 seconds...")
        time.sleep(30)

if __name__ == "__main__":
    run()