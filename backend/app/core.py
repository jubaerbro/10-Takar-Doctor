"""Shared plumbing: configuration, clock, database access, errors and security helpers."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import jwt

# --------------------------------------------------------------------------- config


@dataclass
class Config:
    database_path: str = "data/app.db"
    upload_dir: str = "data/uploads"
    jwt_secret: str = ""
    dev_mode: bool = False          # returns OTPs in API responses and enables /dev routes. Never on in production.
    admin_email: str = ""           # first super admin, created at start-up if missing
    admin_password: str = ""
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    cors_origins: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            database_path=env("DATABASE_PATH", "data/app.db"),
            upload_dir=env("UPLOAD_DIR", "data/uploads"),
            jwt_secret=env("JWT_SECRET", ""),
            dev_mode=env("DEV_MODE", "0") == "1",
            admin_email=env("ADMIN_EMAIL", ""),
            admin_password=env("ADMIN_PASSWORD", ""),
            livekit_url=env("LIVEKIT_URL", ""),
            livekit_api_key=env("LIVEKIT_API_KEY", ""),
            livekit_api_secret=env("LIVEKIT_API_SECRET", ""),
            cors_origins=[o.strip() for o in env("CORS_ORIGINS", "").split(",") if o.strip()],
        )


config = Config()


def configure(new: Config) -> None:
    global config
    if len(new.jwt_secret) < 32:
        if not new.dev_mode:
            raise RuntimeError("JWT_SECRET must be set to a random string of at least 32 characters.")
        new.jwt_secret = new.jwt_secret or "dev-only-secret-" + "0" * 32
    config = new


# --------------------------------------------------------------------------- clock


def now() -> float:
    """Current time in Unix seconds. Tests replace this to move time forward."""
    return time.time()


BD_OFFSET = 6 * 3600  # Bangladesh is UTC+6 all year


def bd_day_start(t: float) -> float:
    """Unix time of the most recent midnight in Bangladesh."""
    return ((t + BD_OFFSET) // 86400) * 86400 - BD_OFFSET


# --------------------------------------------------------------------------- errors


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# --------------------------------------------------------------------------- database

_conn: sqlite3.Connection | None = None
_tx_depth = 0


def connect(path: str) -> None:
    global _conn, _tx_depth
    if _conn is not None:
        _conn.close()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript((Path(__file__).parent / "schema.sql").read_text())
    _tx_depth = 0


def rows(sql: str, *args) -> list[sqlite3.Row]:
    return _conn.execute(sql, args).fetchall()


def one(sql: str, *args) -> sqlite3.Row | None:
    return _conn.execute(sql, args).fetchone()


def run(sql: str, *args) -> int:
    return _conn.execute(sql, args).rowcount


@contextmanager
def tx():
    """All-or-nothing block. Nested use joins the outer transaction."""
    global _tx_depth
    if _tx_depth == 0:
        _conn.execute("BEGIN IMMEDIATE")
    _tx_depth += 1
    try:
        yield
    except BaseException:
        _tx_depth -= 1
        if _tx_depth == 0:
            _conn.execute("ROLLBACK")
        raise
    else:
        _tx_depth -= 1
        if _tx_depth == 0:
            _conn.execute("COMMIT")


def new_id() -> str:
    return uuid.uuid4().hex


def audit(admin_id: str, action: str, target: str | None = None, **detail) -> None:
    run(
        "INSERT INTO audit_log (id, admin_id, action, target, detail, created_at) VALUES (?,?,?,?,?,?)",
        new_id(), admin_id, action, target, json.dumps(detail), now(),
    )


# --------------------------------------------------------------------------- money


def to_poisha(taka: float) -> int:
    return int(round(float(taka) * 100))


def to_taka(poisha: int | None) -> float:
    return round((poisha or 0) / 100, 2)


# --------------------------------------------------------------------------- security

TOKEN_DAYS = 30


def make_token(subject: str, role: str, **extra) -> str:
    payload = {"sub": subject, "role": role, "iat": int(now()), "exp": int(now()) + TOKEN_DAYS * 86400, **extra}
    return jwt.encode(payload, config.jwt_secret, algorithm="HS256")


def read_token(token: str) -> dict:
    try:
        # Expiry is checked against our own clock so tests can move time.
        claims = jwt.decode(token, config.jwt_secret, algorithms=["HS256"], options={"verify_exp": False, "verify_iat": False})
    except jwt.PyJWTError:
        raise ApiError(401, "invalid_token", "Please log in again.")
    if claims.get("exp", 0) < now():
        raise ApiError(401, "token_expired", "Please log in again.")
    return claims


def hash_otp(phone: str, code: str) -> str:
    return hmac.new(config.jwt_secret.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)
