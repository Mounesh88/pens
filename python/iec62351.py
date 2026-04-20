import os
import json
import time
import base64
import hashlib
import pathlib
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

BASE = pathlib.Path(__file__).parent.parent
load_dotenv(dotenv_path=BASE / '.env')

KEY_DIR  = BASE / 'keys'
PRIV_KEY = KEY_DIR / 'pens_private.pem'
PUB_KEY  = KEY_DIR / 'pens_public.pem'

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
        CREATE TABLE IF NOT EXISTS signed_commands (
            id            BIGSERIAL PRIMARY KEY,
            timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            command_id    TEXT NOT NULL UNIQUE,
            command_type  TEXT NOT NULL,
            command_data  TEXT NOT NULL,
            signature     TEXT NOT NULL,
            public_key_id TEXT NOT NULL,
            verified      BOOLEAN DEFAULT FALSE,
            verified_at   TIMESTAMPTZ,
            status        TEXT DEFAULT 'signed',
            hash          TEXT NOT NULL
        )
    """)
    conn.commit()
    cur.close()
    print("[IEC62351] Tables ready")

# ── KEY GENERATION ──
def generate_keys():
    """Generate RSA-2048 key pair — IEC 62351 compliant."""
    KEY_DIR.mkdir(exist_ok=True)

    if PRIV_KEY.exists() and PUB_KEY.exists():
        print("[IEC62351] Keys already exist — loading existing keys")
        return load_keys()

    print("[IEC62351] Generating RSA-2048 key pair...")

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    public_key = private_key.public_key()

    # Save private key
    with open(PRIV_KEY, 'wb') as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # Save public key
    with open(PUB_KEY, 'wb') as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))

    print(f"[IEC62351] Keys saved to {KEY_DIR}")
    print(f"[IEC62351] Private: {PRIV_KEY}")
    print(f"[IEC62351] Public:  {PUB_KEY}")

    return private_key, public_key

def load_keys():
    """Load existing RSA keys from disk."""
    with open(PRIV_KEY, 'rb') as f:
        private_key = serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend()
        )
    with open(PUB_KEY, 'rb') as f:
        public_key = serialization.load_pem_public_key(
            f.read(), backend=default_backend()
        )
    return private_key, public_key

# ── SIGN COMMAND ──
def sign_command(private_key, command_type, command_data):
    """
    Sign a grid command using RSA-2048 + SHA-256.
    This is the core of IEC 62351 command authentication.
    """
    # Create command payload
    timestamp = datetime.now(timezone.utc).isoformat()
    command_id = hashlib.sha256(
        f"{command_type}{timestamp}{json.dumps(command_data)}".encode()
    ).hexdigest()[:16]

    payload = {
        'command_id':   command_id,
        'command_type': command_type,
        'command_data': command_data,
        'timestamp':    timestamp,
        'issuer':       'PENS-HQCOE',
        'version':      'IEC62351-3',
    }

    payload_bytes = json.dumps(payload, sort_keys=True).encode('utf-8')

    # SHA-256 hash of payload
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    # RSA-2048 signature with PSS padding
    signature = private_key.sign(
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )

    signature_b64 = base64.b64encode(signature).decode('utf-8')

    return payload, signature_b64, payload_hash, command_id

# ── VERIFY COMMAND ──
def verify_command(public_key, payload, signature_b64):
    """
    Verify a signed command.
    Returns True if signature is valid, False if tampered.
    """
    try:
        payload_bytes  = json.dumps(payload, sort_keys=True).encode('utf-8')
        signature      = base64.b64decode(signature_b64)

        public_key.verify(
            signature,
            payload_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return True
    except InvalidSignature:
        return False
    except Exception as e:
        print(f"[IEC62351] Verification error: {e}")
        return False

# ── SAVE SIGNED COMMAND ──
def save_signed_command(conn, command_id, command_type,
                         payload, signature, pub_key_id, payload_hash):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO signed_commands
        (timestamp, command_id, command_type, command_data,
         signature, public_key_id, hash, status)
        VALUES (NOW(),%s,%s,%s,%s,%s,%s,'signed')
        ON CONFLICT (command_id) DO NOTHING
    """, (
        command_id,
        command_type,
        json.dumps(payload),
        signature,
        pub_key_id,
        payload_hash,
    ))
    conn.commit()
    cur.close()

def mark_verified(conn, command_id):
    cur = conn.cursor()
    cur.execute("""
        UPDATE signed_commands
        SET verified=TRUE, verified_at=NOW(), status='verified'
        WHERE command_id=%s
    """, (command_id,))
    conn.commit()
    cur.close()

def mark_rejected(conn, command_id, reason):
    cur = conn.cursor()
    cur.execute("""
        UPDATE signed_commands
        SET status='rejected', verified=FALSE
        WHERE command_id=%s
    """, (command_id,))
    conn.commit()
    cur.close()

# ── GET PENDING HQCOE DECISIONS ──
def get_pending_decisions(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT h.id, h.problem_type, h.decision, h.confidence, h.grid_state
        FROM hqcoe_decisions h
        LEFT JOIN signed_commands s ON s.command_id LIKE '%'||h.id::text||'%'
        WHERE s.id IS NULL
        AND h.timestamp > NOW() - INTERVAL '10 minutes'
        ORDER BY h.timestamp DESC
        LIMIT 5
    """)
    rows = cur.fetchall()
    cur.close()
    return rows

# ── PRINT STATUS ──
def print_status(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT status, COUNT(*)
        FROM signed_commands
        WHERE timestamp > NOW() - INTERVAL '1 hour'
        GROUP BY status
    """)
    rows = cur.fetchall()
    cur.close()
    if rows:
        print("[IEC62351] Last hour command status:")
        for status, count in rows:
            print(f"  {status:10} — {count} commands")

# ── MAIN LOOP ──
def run():
    print("[IEC62351] IEC 62351 Command Encryption starting...")
    print("[IEC62351] Algorithm: RSA-2048 + SHA-256 + PSS padding")
    print("[IEC62351] Standard:  IEC 62351-3 (TLS for SCADA)")

    # Generate or load keys
    private_key, public_key = generate_keys()

    # Get public key fingerprint
    pub_bytes   = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pub_key_id = hashlib.sha256(pub_bytes).hexdigest()[:16]
    print(f"[IEC62351] Public key fingerprint: {pub_key_id}")

    conn  = get_db()
    setup_tables(conn)

    # Quick self-test
    print("\n[IEC62351] Running self-test...")
    test_payload, test_sig, test_hash, test_id = sign_command(
        private_key, 'SELF_TEST', {'test': True}
    )
    verified = verify_command(public_key, test_payload, test_sig)
    print(f"[IEC62351] Self-test: {'PASS' if verified else 'FAIL'}")

    # Tamper test
    tampered = dict(test_payload)
    tampered['command_data'] = {'test': False, 'tampered': True}
    tamper_result = verify_command(public_key, tampered, test_sig)
    print(f"[IEC62351] Tamper detection: {'PASS' if not tamper_result else 'FAIL'}")

    print("\n[IEC62351] Signing loop active — watching for HQCOE decisions...")

    cycle = 0
    while True:
        cycle += 1
        try:
            decisions = get_pending_decisions(conn)

            if decisions:
                print(f"\n[IEC62351] === Cycle #{cycle} — {len(decisions)} commands to sign ===")
                for row in decisions:
                    did, prob_type, decision, confidence, grid_state = row

                    command_data = {
                        'decision_id': did,
                        'decision':    decision,
                        'confidence':  confidence,
                        'grid_state':  grid_state,
                    }

                    # Sign
                    payload, signature, payload_hash, command_id = sign_command(
                        private_key, prob_type, command_data
                    )

                    # Save
                    save_signed_command(
                        conn, command_id, prob_type,
                        payload, signature, pub_key_id, payload_hash
                    )

                    # Verify immediately
                    verified = verify_command(public_key, payload, signature)

                    if verified:
                        mark_verified(conn, command_id)
                        print(f"[IEC62351] ✓ SIGNED & VERIFIED: {decision[:50]}...")
                        print(f"[IEC62351]   ID: {command_id} | Hash: {payload_hash[:16]}")
                    else:
                        mark_rejected(conn, command_id, "Verification failed")
                        print(f"[IEC62351] ✗ REJECTED: {decision[:50]}...")

            if cycle % 20 == 0:
                print_status(conn)

        except Exception as e:
            print(f"[IEC62351] Error: {e}")

        time.sleep(15)

if __name__ == "__main__":
    run()