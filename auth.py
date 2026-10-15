"""
auth.py
Minimal username/password authentication using only the Python standard
library (no bcrypt/passlib needed, so there's nothing extra to compile or
install on Windows).

- Passwords are hashed with PBKDF2-HMAC-SHA256 + a random per-user salt.
- Logging in issues a random opaque token stored in the `sessions` table.
- The frontend sends the token back as `Authorization: Bearer <token>`.
"""

import hashlib
import secrets

from fastapi import Header, HTTPException

from database import get_connection

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return salt, pwd_hash


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _, computed = hash_password(password, salt)
    return secrets.compare_digest(computed, expected_hash)


def create_token() -> str:
    return secrets.token_hex(32)


def get_current_user(authorization: str | None = Header(default=None)):
    """FastAPI dependency: reads 'Authorization: Bearer <token>', validates it
    against the sessions table, and returns the user row. Raises 401 if
    missing/invalid so the frontend knows to send the person back to login."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT users.id AS id, users.username AS username
        FROM sessions JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ?
        """,
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return {"id": row["id"], "username": row["username"], "token": token}
