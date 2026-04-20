import os
import jwt
import bcrypt
import psycopg2
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(dotenv_path='../.env')

SECRET_KEY = 'pens-grid-os-quantum-secure-key-2026-california'

# ── DATABASE ──
def get_db():
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="postgres", user="postgres",
        password="pens2026"
    )

# ── CREATE OPERATOR ──
def create_operator(username, password, role='operator'):
    conn = get_db()
    cur  = conn.cursor()

    # Hash password
    pw_hash = bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    try:
        cur.execute("""
            INSERT INTO operators (username, password_hash, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO NOTHING
        """, (username, pw_hash, role))
        conn.commit()
        print(f"[AUTH] Operator created: {username} | Role: {role}")
    except Exception as e:
        print(f"[AUTH] Error creating operator: {e}")
    finally:
        cur.close()
        conn.close()

# ── LOGIN ──
def login(username, password):
    conn = get_db()
    cur  = conn.cursor()

    try:
        # Get operator
        cur.execute("""
            SELECT username, password_hash, role, active
            FROM operators
            WHERE username = %s
        """, (username,))
        row = cur.fetchone()

        if not row:
            print(f"[AUTH] Login failed: {username} not found")
            return None

        uname, pw_hash, role, active = row

        if not active:
            print(f"[AUTH] Login failed: {username} account disabled")
            return None

        # Verify password
        if not bcrypt.checkpw(password.encode('utf-8'), pw_hash.encode('utf-8')):
            print(f"[AUTH] Login failed: wrong password for {username}")
            return None

        # Generate JWT token
        now     = datetime.now(timezone.utc)
        expires = now + timedelta(hours=8)

        payload = {
            'username': uname,
            'role':     role,
            'iat':      now.timestamp(),
            'exp':      expires.timestamp(),
        }

        token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')

        # Save session
        cur.execute("""
            INSERT INTO sessions (username, token, expires_at, role)
            VALUES (%s, %s, %s, %s)
        """, (uname, token, expires, role))

        # Update last login
        cur.execute("""
            UPDATE operators SET last_login = NOW()
            WHERE username = %s
        """, (uname,))

        conn.commit()

        print(f"[AUTH] Login success: {username} | Role: {role}")
        print(f"[AUTH] Token expires: {expires.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        return {
            'token':    token,
            'username': uname,
            'role':     role,
            'expires':  expires.isoformat(),
        }

    except Exception as e:
        print(f"[AUTH] Login error: {e}")
        return None
    finally:
        cur.close()
        conn.close()

# ── VERIFY TOKEN ──
def verify_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        print("[AUTH] Token expired")
        return None
    except jwt.InvalidTokenError:
        print("[AUTH] Invalid token")
        return None

# ── LIST SESSIONS ──
def list_sessions():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT username, role, timestamp, expires_at, active
        FROM sessions
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# ── SETUP DEFAULT OPERATORS ──
def setup_defaults():
    """Create default operators if they don't exist."""
    defaults = [
        ('admin',    'pens-admin-2026',    'admin'),
        ('operator', 'pens-operator-2026', 'operator'),
        ('regulator','pens-regulator-2026','regulator'),
    ]
    for username, password, role in defaults:
        create_operator(username, password, role)

if __name__ == "__main__":
    print("[AUTH] Setting up PENS authentication system...")
    setup_defaults()

    print("\n[AUTH] Testing login...")
    result = login('admin', 'pens-admin-2026')
    if result:
        print(f"[AUTH] Token: {result['token'][:40]}...")
        print("\n[AUTH] Verifying token...")
        payload = verify_token(result['token'])
        if payload:
            print(f"[AUTH] Verified: {payload['username']} | Role: {payload['role']}")

    print("\n[AUTH] Active sessions:")
    for s in list_sessions():
        print(f"  {s[0]:12} | {s[1]:10} | {s[4]}")

    print("\n[AUTH] Default credentials:")
    print("  admin     / pens-admin-2026")
    print("  operator  / pens-operator-2026")
    print("  regulator / pens-regulator-2026")