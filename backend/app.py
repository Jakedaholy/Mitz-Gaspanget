#!/usr/bin/env python3
"""
Mitz MLBB Checker — hardened API gateway.
Real upstream stays on the server. Clients only hit this API with a key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, g, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

APP_SECRET = os.environ.get("MITZ_SECRET", secrets.token_hex(32))
ADMIN_TOKEN = os.environ.get("MITZ_ADMIN_TOKEN", secrets.token_hex(24))
DB_PATH = os.environ.get("MITZ_DB", str(Path(__file__).parent / "mitz.db"))
UPSTREAM_BASE = os.environ.get("MITZ_UPSTREAM", "https://checkton.online/backend")
UPSTREAM_KEY = os.environ.get("MITZ_UPSTREAM_KEY", "")
RATE_DEFAULT = "60 per minute"
COST_DEVICE_SINGLE = 1
COST_DEVICE_BULK = 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mitz")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["SECRET_KEY"] = APP_SECRET
app.config["JSON_SORT_KEYS"] = False

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[RATE_DEFAULT],
    storage_uri="memory://",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    username    TEXT,
    role        TEXT DEFAULT 'user',
    credits     REAL DEFAULT 0,
    api_key     TEXT UNIQUE,
    key_hash    TEXT UNIQUE,
    revoked     INTEGER DEFAULT 0,
    created_at  TEXT,
    last_used   TEXT
);

CREATE TABLE IF NOT EXISTS usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    endpoint    TEXT,
    cost        REAL,
    ip          TEXT,
    status      TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT,
    detail      TEXT,
    created_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_key_hash ON users(key_hash);
CREATE INDEX IF NOT EXISTS idx_users_tg ON users(telegram_id);
"""

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)
    log.info("DB ready -> %s", DB_PATH)

def _hash_key(raw: str) -> str:
    return hashlib.sha256((raw + APP_SECRET).encode()).hexdigest()

def generate_api_key() -> str:
    raw = secrets.token_hex(16).upper()
    parts = [raw[i : i + 4] for i in range(0, 16, 4)]
    return "MITZ-" + "-".join(parts)

def sign_response(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hmac.new(APP_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]

def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = (
            request.headers.get("X-Api-Key")
            or request.headers.get("x-api-key")
            or request.args.get("key")
            or (request.get_json(silent=True) or {}).get("api_key")
        )
        if not key:
            return jsonify({"ok": False, "error": "missing_api_key"}), 401
        h = _hash_key(key.strip())
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE key_hash=? AND revoked=0", (h,)
            ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "invalid_or_revoked_key"}), 401
        g.user = dict(row)
        g.api_key_raw = key.strip()
        with db() as conn:
            conn.execute(
                "UPDATE users SET last_used=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )
        return f(*args, **kwargs)
    return wrapper

def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Token") or request.args.get("admin")
        if not token or not hmac.compare_digest(token, ADMIN_TOKEN):
            return jsonify({"ok": False, "error": "forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper

def deduct_credits(user_id: int, cost: float, endpoint: str) -> Tuple[bool, float]:
    with db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or row["credits"] < cost:
            return False, float(row["credits"] if row else 0)
        new_bal = round(row["credits"] - cost, 4)
        conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, user_id))
        conn.execute(
            "INSERT INTO usage_log (user_id, endpoint, cost, ip, status, created_at) VALUES (?,?,?,?,?,?)",
            (
                user_id,
                endpoint,
                cost,
                request.remote_addr or "",
                "ok",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return True, new_bal

import requests as _req

_SESSION = _req.Session()
_SESSION.headers.update(
    {
        "User-Agent": "Mitz-Gateway/1.0",
        "Content-Type": "application/json",
        "x-api-key": UPSTREAM_KEY,
    }
)

DEVICE_ID_RE = re.compile(r"^(and_|ios_)[A-Za-z0-9\-]+", re.IGNORECASE)

def upstream_device_id(device_id: str, mode: str = "valid") -> Dict[str, Any]:
    if not UPSTREAM_KEY:
        return {"status": -1, "msg": "upstream_not_configured"}
    try:
        r = _SESSION.post(
            f"{UPSTREAM_BASE}/device_id",
            json={"device_id": device_id, "mode": mode},
            timeout=25,
        )
        return r.json()
    except Exception as e:
        log.warning("upstream device_id error: %s", e)
        return {"status": -1, "msg": "upstream_error"}

def upstream_info(role_id: str, zone_id: str) -> Dict[str, Any]:
    if not UPSTREAM_KEY:
        return {"status": -1, "msg": "upstream_not_configured"}
    try:
        r = _SESSION.post(
            f"{UPSTREAM_BASE}/info",
            json={"role_id": str(role_id), "zone_id": str(zone_id), "type": "lookup"},
            timeout=25,
        )
        return r.json()
    except Exception as e:
        log.warning("upstream info error: %s", e)
        return {"status": -1, "msg": "upstream_error"}

@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "mitz", "ts": int(time.time())})

@app.route("/api/v1/balance", methods=["GET", "POST"])
@limiter.limit("30 per minute")
@require_api_key
def balance():
    u = g.user
    payload = {
        "ok": True,
        "credits": float(u["credits"]),
        "role": u["role"],
        "username": u.get("username") or "",
    }
    payload["sig"] = sign_response(payload)
    return jsonify(payload)

@app.route("/api/v1/device/check", methods=["POST"])
@limiter.limit("40 per minute")
@require_api_key
def device_check():
    body = request.get_json(silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    mode = (body.get("mode") or "valid").strip().lower()
    if mode not in ("valid", "ban", "ban_info"):
        mode = "valid"
    if not device_id or not DEVICE_ID_RE.match(device_id):
        return jsonify({"ok": False, "error": "invalid_device_id"}), 400
    ok, bal = deduct_credits(g.user["id"], COST_DEVICE_SINGLE, "device/check")
    if not ok:
        return jsonify({"ok": False, "error": "no_credits", "credits": bal}), 402
    api_mode = "ban" if mode in ("ban", "ban_info") else "valid"
    raw = upstream_device_id(device_id, mode=api_mode)
    status = raw.get("status")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    result = str(data.get("result") or ("valid" if status == 0 else "invalid")).lower()
    out: Dict[str, Any] = {
        "ok": status == 0,
        "device_id": device_id,
        "result": result,
        "credits": bal,
    }
    if status == 0 and data:
        out["role_id"] = data.get("account_id") or data.get("role_id") or data.get("uid")
        out["zone_id"] = data.get("zone_id")
        out["country"] = data.get("country") or data.get("created_country")
        out["created"] = data.get("created")
        ban = data.get("ban") or {}
        if result == "banned" or ban:
            out["ban"] = {
                "reason": ban.get("reason") or ban.get("ban_reason") or "yes",
                "until": ban.get("until") or ban.get("ban_until") or "N/A",
            }
        if mode == "ban_info" and out.get("role_id") and out.get("zone_id"):
            info = upstream_info(str(out["role_id"]), str(out["zone_id"]))
            if info.get("status") == 0 or info.get("code") == 0:
                idata = info.get("data")
                if isinstance(idata, list) and idata:
                    idata = idata[0] if isinstance(idata[0], dict) else {}
                if isinstance(idata, dict):
                    out["info"] = {
                        "name": idata.get("name") or idata.get("nickname"),
                        "level": idata.get("level"),
                        "rank_level": idata.get("rank_level"),
                        "country": idata.get("country") or idata.get("reg_country"),
                        "last_login": idata.get("last_login"),
                        "device_count": idata.get("device_count"),
                        "is_banned": idata.get("is_banned", False),
                    }
    out["sig"] = sign_response(out)
    return jsonify(out)

@app.route("/api/v1/device/bulk", methods=["POST"])
@limiter.limit("10 per minute")
@require_api_key
def device_bulk():
    body = request.get_json(silent=True) or {}
    devices = body.get("devices") or []
    mode = (body.get("mode") or "valid").strip().lower()
    if mode not in ("valid", "ban"):
        mode = "valid"
    if not isinstance(devices, list) or not devices:
        return jsonify({"ok": False, "error": "empty_devices"}), 400
    if len(devices) > 50:
        return jsonify({"ok": False, "error": "max_50_per_request"}), 400
    cleaned = []
    for d in devices:
        d = str(d).strip()
        if DEVICE_ID_RE.match(d):
            cleaned.append(d)
    if not cleaned:
        return jsonify({"ok": False, "error": "no_valid_device_ids"}), 400
    cost = COST_DEVICE_BULK * len(cleaned)
    ok, bal = deduct_credits(g.user["id"], cost, "device/bulk")
    if not ok:
        return jsonify({"ok": False, "error": "no_credits", "credits": bal, "needed": cost}), 402
    results = []
    for did in cleaned:
        raw = upstream_device_id(did, mode="ban" if mode == "ban" else "valid")
        status = raw.get("status")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        result = str(data.get("result") or ("valid" if status == 0 else "invalid")).lower()
        item = {
            "device_id": did,
            "ok": status == 0,
            "result": result,
            "role_id": data.get("account_id") or data.get("role_id") or data.get("uid"),
            "zone_id": data.get("zone_id"),
        }
        if result == "banned":
            ban = data.get("ban") or {}
            item["ban"] = {
                "reason": ban.get("reason") or ban.get("ban_reason") or "yes",
                "until": ban.get("until") or ban.get("ban_until") or "N/A",
            }
        results.append(item)
    out = {"ok": True, "count": len(results), "results": results, "credits": bal}
    out["sig"] = sign_response(out)
    return jsonify(out)

@app.route("/api/v1/admin/create_key", methods=["POST"])
@require_admin
def admin_create_key():
    body = request.get_json(silent=True) or {}
    tg_id = body.get("telegram_id")
    username = (body.get("username") or "").strip()[:64]
    credits = float(body.get("credits") or 0)
    role = body.get("role") or "user"
    if role not in ("user", "owner", "vip"):
        role = "user"
    if not tg_id:
        return jsonify({"ok": False, "error": "telegram_id_required"}), 400
    with db() as conn:
        existing = conn.execute(
            "SELECT id, api_key, revoked FROM users WHERE telegram_id=?", (int(tg_id),)
        ).fetchone()
        if existing and not existing["revoked"]:
            return jsonify(
                {
                    "ok": True,
                    "api_key": existing["api_key"],
                    "credits": float(
                        conn.execute(
                            "SELECT credits FROM users WHERE id=?", (existing["id"],)
                        ).fetchone()["credits"]
                    ),
                    "message": "existing_key",
                }
            )
        raw_key = generate_api_key()
        h = _hash_key(raw_key)
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            conn.execute(
                "UPDATE users SET api_key=?, key_hash=?, credits=?, role=?, revoked=0, username=?, created_at=? WHERE id=?",
                (raw_key, h, credits, role, username, now, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO users (telegram_id, username, role, credits, api_key, key_hash, revoked, created_at) VALUES (?,?,?,?,?,?,0,?)",
                (int(tg_id), username, role, credits, raw_key, h, now),
            )
        conn.execute(
            "INSERT INTO admin_actions (action, detail, created_at) VALUES (?,?,?)",
            ("create_key", f"tg={tg_id} credits={credits}", now),
        )
    return jsonify({"ok": True, "api_key": raw_key, "credits": credits, "role": role})

@app.route("/api/v1/admin/revoke_key", methods=["POST"])
@require_admin
def admin_revoke_key():
    body = request.get_json(silent=True) or {}
    tg_id = body.get("telegram_id")
    if not tg_id:
        return jsonify({"ok": False, "error": "telegram_id_required"}), 400
    with db() as conn:
        conn.execute("UPDATE users SET revoked=1 WHERE telegram_id=?", (int(tg_id),))
        conn.execute(
            "INSERT INTO admin_actions (action, detail, created_at) VALUES (?,?,?)",
            ("revoke_key", f"tg={tg_id}", datetime.now(timezone.utc).isoformat()),
        )
    return jsonify({"ok": True, "message": "revoked"})

@app.route("/api/v1/admin/add_credits", methods=["POST"])
@require_admin
def admin_add_credits():
    body = request.get_json(silent=True) or {}
    tg_id = body.get("telegram_id")
    amount = float(body.get("amount") or 0)
    if not tg_id or amount == 0:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    with db() as conn:
        row = conn.execute(
            "SELECT id, credits FROM users WHERE telegram_id=?", (int(tg_id),)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        new_bal = round(row["credits"] + amount, 4)
        conn.execute("UPDATE users SET credits=? WHERE id=?", (new_bal, row["id"]))
    return jsonify({"ok": True, "credits": new_bal})

@app.route("/api/v1/admin/stats", methods=["GET"])
@require_admin
def admin_stats():
    with db() as conn:
        users = conn.execute("SELECT COUNT(*) AS c FROM users WHERE revoked=0").fetchone()["c"]
        total_credits = conn.execute(
            "SELECT COALESCE(SUM(credits),0) AS s FROM users WHERE revoked=0"
        ).fetchone()["s"]
        usage = conn.execute("SELECT COUNT(*) AS c FROM usage_log").fetchone()["c"]
    return jsonify(
        {"ok": True, "active_users": users, "total_credits": total_credits, "usage_events": usage}
    )

@app.after_request
def harden(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"ok": False, "error": "rate_limited"}), 429

@app.errorhandler(Exception)
def catch_all(e):
    log.exception("unhandled")
    return jsonify({"ok": False, "error": "internal"}), 500

if __name__ == "__main__":
    init_db()
    seed_tg = os.environ.get("MITZ_OWNER_TG")
    seed_credits = float(os.environ.get("MITZ_OWNER_CREDITS", "99999"))
    if seed_tg:
        with db() as conn:
            exists = conn.execute(
                "SELECT id FROM users WHERE telegram_id=?", (int(seed_tg),)
            ).fetchone()
            if not exists:
                raw = generate_api_key()
                conn.execute(
                    "INSERT INTO users (telegram_id, username, role, credits, api_key, key_hash, revoked, created_at) VALUES (?,?,?,?,?,?,0,?)",
                    (
                        int(seed_tg),
                        "owner",
                        "owner",
                        seed_credits,
                        raw,
                        _hash_key(raw),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                log.info("Seeded owner key for TG %s -> %s", seed_tg, raw)
    port = int(os.environ.get("PORT", 8080))
    log.info("Mitz gateway listening on :%s", port)
    app.run(host="0.0.0.0", port=port, debug=False)
