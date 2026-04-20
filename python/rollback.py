import os
import time
import psycopg2
import pathlib
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

BASE = pathlib.Path(__file__).parent.parent
load_dotenv(dotenv_path=BASE / '.env')

def get_db():
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="postgres", user="postgres",
        password="pens2026"
    )

def setup_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rollback_log (
            id            BIGSERIAL PRIMARY KEY,
            timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            command_id    BIGINT,
            decision      TEXT NOT NULL,
            window_start  TIMESTAMPTZ NOT NULL,
            window_end    TIMESTAMPTZ NOT NULL,
            status        TEXT DEFAULT 'in_window',
            rolled_back   BOOLEAN DEFAULT FALSE,
            rolled_back_at TIMESTAMPTZ,
            rolled_back_by TEXT,
            executed_at   TIMESTAMPTZ,
            reason        TEXT
        )
    """)
    conn.commit()
    cur.close()
    print("[ROLLBACK] Tables ready")

def register_command(conn, decision_id, decision):
    """Register a new command with a 30-second rollback window."""
    cur  = conn.cursor()
    now  = datetime.now(timezone.utc)
    end  = now + timedelta(seconds=30)

    cur.execute("""
        INSERT INTO rollback_log
        (timestamp, command_id, decision, window_start, window_end, status)
        VALUES (%s, %s, %s, %s, %s, 'in_window')
        RETURNING id
    """, (now, decision_id, decision, now, end))

    rid = cur.fetchone()[0]
    conn.commit()
    cur.close()

    print(f"[ROLLBACK] Command registered — ID: {rid}")
    print(f"[ROLLBACK] Decision: {decision[:60]}...")
    print(f"[ROLLBACK] Rollback window: {now.strftime('%H:%M:%S')} → {end.strftime('%H:%M:%S')}")
    return rid

def rollback_command(conn, rollback_id, rolled_back_by='operator', reason='Manual rollback'):
    """Cancel a command within its rollback window."""
    cur = conn.cursor()

    # Check if still in window
    cur.execute("""
        SELECT status, window_end, decision
        FROM rollback_log
        WHERE id = %s
    """, (rollback_id,))
    row = cur.fetchone()

    if not row:
        print(f"[ROLLBACK] Command {rollback_id} not found")
        return False

    status, window_end, decision = row
    now = datetime.now(timezone.utc)

    if status != 'in_window':
        print(f"[ROLLBACK] Cannot rollback — status is {status}")
        return False

    if now > window_end:
        print(f"[ROLLBACK] Rollback window expired — command already executed")
        return False

    # Perform rollback
    cur.execute("""
        UPDATE rollback_log
        SET status = 'rolled_back',
            rolled_back = TRUE,
            rolled_back_at = NOW(),
            rolled_back_by = %s,
            reason = %s
        WHERE id = %s
    """, (rolled_back_by, reason, rollback_id))

    conn.commit()
    cur.close()

    print(f"[ROLLBACK] ✓ Command rolled back successfully")
    print(f"[ROLLBACK] Decision cancelled: {decision[:60]}...")
    return True

def process_windows(conn):
    """
    Check all commands in their rollback window.
    Execute commands whose window has passed without rollback.
    """
    cur = conn.cursor()

    # Find commands whose window has expired — execute them
    cur.execute("""
        SELECT id, decision, command_id
        FROM rollback_log
        WHERE status = 'in_window'
        AND window_end < NOW()
        AND rolled_back = FALSE
    """)
    ready = cur.fetchall()

    for rid, decision, cmd_id in ready:
        cur.execute("""
            UPDATE rollback_log
            SET status = 'executed',
                executed_at = NOW(),
                reason = 'Rollback window passed — command executed'
            WHERE id = %s
        """, (rid,))
        print(f"[ROLLBACK] ✓ EXECUTED: {decision[:60]}...")
        print(f"[ROLLBACK]   Window passed — command sent to grid")

    # Find commands still in window
    cur.execute("""
        SELECT id, decision, window_end
        FROM rollback_log
        WHERE status = 'in_window'
        AND rolled_back = FALSE
    """)
    pending = cur.fetchall()

    for rid, decision, window_end in pending:
        now       = datetime.now(timezone.utc)
        remaining = (window_end - now).total_seconds()
        if remaining > 0:
            print(f"[ROLLBACK] ⏱ In window: {decision[:50]}... ({remaining:.0f}s remaining)")

    conn.commit()
    cur.close()

    return len(ready)

def register_new_approvals(conn):
    """Pick up newly approved HQCOE decisions and register them for rollback."""
    cur = conn.cursor()

    cur.execute("""
        SELECT a.id, a.decision_id, a.decision
        FROM approval_queue a
        LEFT JOIN rollback_log r ON r.command_id = a.decision_id
        WHERE a.status = 'approved'
        AND r.id IS NULL
        AND a.timestamp > NOW() - INTERVAL '10 minutes'
        ORDER BY a.timestamp DESC
    """)
    new = cur.fetchall()
    cur.close()

    for aid, did, decision in new:
        register_command(conn, did, decision)

def print_status(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT status, COUNT(*)
        FROM rollback_log
        WHERE timestamp > NOW() - INTERVAL '1 hour'
        GROUP BY status
    """)
    rows = cur.fetchall()
    cur.close()

    if rows:
        print("[ROLLBACK] Last hour summary:")
        for status, count in rows:
            print(f"  {status:12} — {count} commands")

def run():
    print("[ROLLBACK] 30-second command rollback system starting...")
    print("[ROLLBACK] Every approved command has a 30s cancellation window")
    print("[ROLLBACK] After 30s — command executes automatically")

    conn  = get_db()
    setup_tables(conn)

    cycle = 0

    while True:
        cycle += 1

        try:
            # Pick up new approved decisions
            register_new_approvals(conn)

            # Process windows
            executed = process_windows(conn)

            # Print status every 10 cycles
            if cycle % 10 == 0:
                print_status(conn)

        except Exception as e:
            print(f"[ROLLBACK] Error: {e}")

        time.sleep(5)

if __name__ == "__main__":
    run()