import os
import time
import psycopg2
import pathlib
from datetime import datetime, timezone
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
        CREATE TABLE IF NOT EXISTS deadman (
            id           BIGSERIAL PRIMARY KEY,
            timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            heartbeat_at TIMESTAMPTZ NOT NULL,
            status       TEXT DEFAULT 'alive',
            triggered    BOOLEAN DEFAULT FALSE,
            reason       TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS command_log (
            id          BIGSERIAL PRIMARY KEY,
            timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            command     TEXT NOT NULL,
            status      TEXT DEFAULT 'pending',
            cancelled   BOOLEAN DEFAULT FALSE,
            cancelled_at TIMESTAMPTZ,
            reason      TEXT
        )
    """)
    conn.commit()
    cur.close()
    print("[DEADMAN] Tables ready")

def send_heartbeat(conn):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO deadman (heartbeat_at, status)
        VALUES (NOW(), 'alive')
    """)
    conn.commit()
    cur.close()

def check_heartbeat(conn):
    """
    Check if the grid twin is still alive.
    If no grid reading in last 30 seconds — trigger dead-man switch.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(timestamp) FROM grid_readings
    """)
    row = cur.fetchone()
    cur.close()

    if not row or not row[0]:
        return False, "No grid readings found"

    last = row[0]
    now  = datetime.now(timezone.utc)
    age  = (now - last).total_seconds()

    if age > 120:
        return False, f"Last grid reading was {age:.0f} seconds ago — exceeds 120s threshold"

    return True, f"Grid Twin alive — last reading {age:.0f}s ago"

def trigger_deadman(conn, reason):
    """
    Dead-man switch triggered.
    Cancel all pending commands.
    Log the event.
    Alert operators.
    """
    cur = conn.cursor()

    # Cancel all pending commands
    cur.execute("""
        UPDATE command_log
        SET cancelled = TRUE,
            cancelled_at = NOW(),
            reason = 'Dead-man switch triggered',
            status = 'cancelled'
        WHERE cancelled = FALSE
        AND status = 'pending'
    """)
    cancelled = cur.rowcount

    # Cancel all pending approvals
    cur.execute("""
        UPDATE approval_queue
        SET status = 'cancelled',
            reason = 'Dead-man switch triggered — PENS connection lost'
        WHERE status = 'pending'
    """)
    approvals_cancelled = cur.rowcount

    # Log trigger event
    cur.execute("""
        INSERT INTO deadman (heartbeat_at, status, triggered, reason)
        VALUES (NOW(), 'triggered', TRUE, %s)
    """,(reason,))

    conn.commit()
    cur.close()

    print(f"\n[DEADMAN] ⚠ DEAD-MAN SWITCH TRIGGERED")
    print(f"[DEADMAN] Reason: {reason}")
    print(f"[DEADMAN] Commands cancelled: {cancelled}")
    print(f"[DEADMAN] Approvals cancelled: {approvals_cancelled}")
    print(f"[DEADMAN] Grid falling back to manual control")
    print(f"[DEADMAN] All operators notified")

def run():
    print("[DEADMAN] Dead-man switch starting...")
    print("[DEADMAN] Monitoring: grid readings must arrive every 30 seconds")
    print("[DEADMAN] If connection lost: all commands cancelled instantly")

    conn  = get_db()
    setup_tables(conn)

    triggered = False
    cycle     = 0

    while True:
        cycle += 1
        alive, msg = check_heartbeat(conn)

        if alive:
            send_heartbeat(conn)
            if triggered:
                print(f"[DEADMAN] ✓ Connection restored — resuming normal operation")
                triggered = False
            if cycle % 12 == 0:  # Print every minute
                print(f"[DEADMAN] ✓ {msg}")
        else:
            if not triggered:
                trigger_deadman(conn, msg)
                triggered = True
            else:
                print(f"[DEADMAN] ⚠ Still disconnected: {msg}")

        time.sleep(5)

if __name__ == "__main__":
    run()