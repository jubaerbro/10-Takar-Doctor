"""Admin panel API: login, settings, doctor verification, wallet adjustments, reports, suspensions, audit log.

Admin roles:  super_admin (everything) · verifier (doctor verification) · support_finance (money, reports, suspensions)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from starlette.responses import FileResponse

from . import bookings, core, settings_store, wallet
from .api import hub, route
from .core import ApiError
from .matching import LIVE_FOR_PATIENT, drop_doctor, release_unconnected

SUPER = ("super_admin",)
VERIFY = ("verifier",)
SUPPORT = ("support_finance",)
FAILED_LOGINS: dict[str, list[float]] = {}   # email -> times of recent wrong passwords
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 900


def ensure_first_admin() -> None:
    cfg = core.config
    if cfg.admin_email and cfg.admin_password and not core.one("SELECT 1 FROM admins LIMIT 1"):
        core.run("INSERT INTO admins (id, email, password_hash, admin_role, created_at) VALUES (?,?,?,?,?)",
                 core.new_id(), cfg.admin_email.lower(), core.hash_password(cfg.admin_password), "super_admin", core.now())


class AdminLogin(BaseModel):
    email: str
    password: str


@route("/admin/login", ["POST"], body=AdminLogin)
async def admin_login(request, user, data: AdminLogin):
    email, now = data.email.strip().lower(), core.now()
    recent = [t for t in FAILED_LOGINS.get(email, []) if now - t < LOGIN_LOCK_SECONDS]
    if len(recent) >= LOGIN_MAX_FAILURES:
        raise ApiError(429, "login_locked", "Too many wrong passwords. Try again in 15 minutes.")
    row = core.one("SELECT * FROM admins WHERE email = ?", email)
    if not row or row["status"] != "active" or not core.verify_password(data.password, row["password_hash"]):
        FAILED_LOGINS[email] = recent + [now]
        raise ApiError(401, "bad_login", "Wrong email or password.")
    FAILED_LOGINS.pop(email, None)
    return {"token": core.make_token(row["id"], "admin"), "admin_role": row["admin_role"], "email": row["email"]}


class NewAdmin(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10)
    admin_role: Literal["super_admin", "verifier", "support_finance"]


@route("/admin/admins", ["POST"], roles=("admin",), admin_roles=SUPER, body=NewAdmin)
async def create_admin(request, admin, data: NewAdmin):
    if core.one("SELECT 1 FROM admins WHERE email = ?", data.email.lower()):
        raise ApiError(409, "exists", "An admin with this email already exists.")
    admin_id = core.new_id()
    core.run("INSERT INTO admins (id, email, password_hash, admin_role, created_at) VALUES (?,?,?,?,?)",
             admin_id, data.email.lower(), core.hash_password(data.password), data.admin_role, core.now())
    core.audit(admin["id"], "admin_created", admin_id, email=data.email.lower(), admin_role=data.admin_role)
    return {"id": admin_id, "email": data.email.lower(), "admin_role": data.admin_role}


@route("/admin/me", ["GET"], roles=("admin",))
async def admin_me(request, admin, data):
    return {"id": admin["id"], "email": admin["email"], "admin_role": admin["admin_role"]}


@route("/admin/admins", ["GET"], roles=("admin",), admin_roles=SUPER)
async def list_admins(request, admin, data):
    found = core.rows("SELECT id, email, admin_role, status, created_at FROM admins ORDER BY created_at")
    return {"admins": [dict(a) for a in found]}


# --------------------------------------------------------------------------- settings


@route("/admin/settings", ["GET"], roles=("admin",))
async def get_settings(request, admin, data):
    return settings_store.all_settings()


@route("/admin/settings", ["PUT"], roles=("admin",), admin_roles=SUPER)
async def put_settings(request, admin, data):
    """Body: any subset of settings, for example {"instant_call_price_taka": 12}. Applies to new calls only."""
    try:
        changes = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        raise ApiError(400, "bad_json", "The request body is not valid JSON.")
    if not isinstance(changes, dict) or not changes:
        raise ApiError(422, "invalid_input", "Send the settings to change as a JSON object.")
    return settings_store.update(changes, admin["id"])


# --------------------------------------------------------------------------- doctor verification


def doctor_admin_view(d) -> dict:
    user = core.one("SELECT phone, status, created_at FROM users WHERE id = ?", d["user_id"])
    docs = core.rows("SELECT id, doc_type, content_type, created_at FROM doctor_documents WHERE doctor_id = ? ORDER BY created_at", d["user_id"])
    return {
        "id": d["user_id"], "phone": user["phone"], "account_status": user["status"], "real_name": d["real_name"],
        "bmdc_number": d["bmdc_number"], "gender": d["gender"], "specialty": d["specialty"], "designation": d["designation"],
        "experience_years": d["experience_years"], "languages": json.loads(d["languages"]), "status": d["status"],
        "review_reason": d["review_reason"], "reviewed_at": d["reviewed_at"], "documents": [dict(x) for x in docs],
    }


@route("/admin/doctors", ["GET"], roles=("admin",), admin_roles=VERIFY + SUPPORT)
async def list_doctors(request, admin, data):
    status = request.query_params.get("status", "pending")
    found = core.rows("SELECT * FROM doctor_profiles WHERE status = ? ORDER BY updated_at ASC LIMIT 200", status)
    return {"doctors": [doctor_admin_view(d) for d in found]}


@route("/admin/doctors/{doctor_id}", ["GET"], roles=("admin",), admin_roles=VERIFY + SUPPORT)
async def get_doctor(request, admin, data):
    d = core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", request.path_params["doctor_id"])
    if not d:
        raise ApiError(404, "not_found", "Doctor not found.")
    return doctor_admin_view(d)


@route("/admin/documents/{document_id}/file", ["GET"], roles=("admin",), admin_roles=VERIFY)
async def get_document_file(request, admin, data):
    doc = core.one("SELECT * FROM doctor_documents WHERE id = ?", request.path_params["document_id"])
    path = Path(core.config.upload_dir) / doc["file_name"] if doc else None
    if not path or not path.is_file():
        raise ApiError(404, "not_found", "Document not found.")
    core.audit(admin["id"], "document_viewed", doc["id"], doctor_id=doc["doctor_id"])
    return FileResponse(path, media_type=doc["content_type"])


class Review(BaseModel):
    action: Literal["approve", "reject", "request_changes"]
    reason: str = Field(default="", max_length=500)
    designation: str | None = None   # required to approve: sets the doctor's pay tier
    specialty: str | None = None     # optional correction


@route("/admin/doctors/{doctor_id}/review", ["POST"], roles=("admin",), admin_roles=VERIFY, body=Review)
async def review_doctor(request, admin, data: Review):
    doctor_id = request.path_params["doctor_id"]
    d = core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", doctor_id)
    if not d:
        raise ApiError(404, "not_found", "Doctor not found.")
    if data.action == "approve":
        if d["status"] not in ("pending", "approved"):
            raise ApiError(409, "not_pending", "This doctor has not submitted for verification.")
        if data.designation not in settings_store.DESIGNATIONS:
            raise ApiError(422, "invalid_designation", f"Choose a designation: {', '.join(settings_store.DESIGNATIONS)}.")
        specialty = data.specialty or d["specialty"]
        if specialty not in settings_store.get("specialties"):
            raise ApiError(422, "invalid_specialty", "Choose a specialty from the list.")
        core.run("UPDATE doctor_profiles SET status = 'approved', designation = ?, specialty = ?, review_reason = NULL, "
                 "reviewed_by = ?, reviewed_at = ? WHERE user_id = ?", data.designation, specialty, admin["id"], core.now(), doctor_id)
    else:
        if d["status"] != "pending":
            raise ApiError(409, "not_pending", "This doctor is not waiting for review.")
        if not data.reason.strip():
            raise ApiError(422, "reason_required", "Give the doctor a reason.")
        new_status = "rejected" if data.action == "reject" else "changes_requested"
        core.run("UPDATE doctor_profiles SET status = ?, review_reason = ?, reviewed_by = ?, reviewed_at = ?, online = 0 WHERE user_id = ?",
                 new_status, data.reason.strip(), admin["id"], core.now(), doctor_id)
    core.audit(admin["id"], f"doctor_{data.action}", doctor_id, reason=data.reason, designation=data.designation)
    await hub.send(doctor_id, "verification", {"status": core.one("SELECT status FROM doctor_profiles WHERE user_id = ?", doctor_id)["status"]})
    return doctor_admin_view(core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", doctor_id))


# --------------------------------------------------------------------------- support and finance


def user_summary(user_id: str | None) -> dict | None:
    """One line about a user for admin lists: who they are, without their documents."""
    u = core.one("SELECT * FROM users WHERE id = ?", user_id) if user_id else None
    if not u:
        return None
    out = {"id": u["id"], "role": u["role"], "phone": u["phone"], "alias": u["alias"], "status": u["status"],
           "balance_taka": core.to_taka(u["balance_poisha"]), "created_at": u["created_at"], "name": None, "doctor_status": None}
    if u["role"] == "doctor":
        d = core.one("SELECT real_name, status, designation FROM doctor_profiles WHERE user_id = ?", u["id"])
        if d:
            out.update(name=d["real_name"], doctor_status=d["status"], designation=d["designation"])
    else:
        p = core.one("SELECT real_name FROM patient_profiles WHERE user_id = ?", u["id"])
        if p:
            out["name"] = p["real_name"]
    return out


@route("/admin/users", ["GET"], roles=("admin",), admin_roles=SUPPORT)
async def search_users(request, admin, data):
    """Find users by part of a phone number, nickname or name. With no search text: the newest accounts."""
    q = request.query_params.get("q", "").strip()
    if q:
        like = f"%{q}%"
        found = core.rows(
            "SELECT u.id FROM users u LEFT JOIN doctor_profiles d ON d.user_id = u.id LEFT JOIN patient_profiles p ON p.user_id = u.id "
            "WHERE u.phone LIKE ? OR u.alias LIKE ? OR d.real_name LIKE ? OR p.real_name LIKE ? OR u.id = ? "
            "ORDER BY u.created_at DESC LIMIT 50", like, like, like, like, q)
    else:
        found = core.rows("SELECT id FROM users ORDER BY created_at DESC LIMIT 50")
    return {"users": [user_summary(r["id"]) for r in found]}


@route("/admin/users/{user_id}", ["GET"], roles=("admin",), admin_roles=SUPPORT)
async def get_user(request, admin, data):
    summary = user_summary(request.path_params["user_id"])
    if not summary:
        raise ApiError(404, "not_found", "User not found.")
    txns = core.rows("SELECT type, amount_poisha, balance_after_poisha, note, created_at FROM wallet_transactions "
                     "WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 30", summary["id"])
    column = "doctor_id" if summary["role"] == "doctor" else "patient_id"
    calls = core.one(f"SELECT COUNT(*) AS n FROM consultations WHERE {column} = ? AND status = 'ended'", summary["id"])["n"]
    reports = core.one("SELECT COUNT(*) AS n FROM reports WHERE target_id = ?", summary["id"])["n"]
    return {**summary, "completed_calls": calls, "reports_against": reports,
            "transactions": [{"type": t["type"], "amount_taka": core.to_taka(t["amount_poisha"]),
                              "balance_after_taka": core.to_taka(t["balance_after_poisha"]), "note": t["note"],
                              "created_at": t["created_at"]} for t in txns]}


class Suspend(BaseModel):
    suspended: bool
    reason: str = Field(min_length=2, max_length=500)


@route("/admin/users/{user_id}/suspend", ["POST"], roles=("admin",), admin_roles=SUPPORT, body=Suspend)
async def suspend_user(request, admin, data: Suspend):
    user_id = request.path_params["user_id"]
    if not core.one("SELECT 1 FROM users WHERE id = ?", user_id):
        raise ApiError(404, "not_found", "User not found.")
    core.run("UPDATE users SET status = ? WHERE id = ?", "suspended" if data.suspended else "active", user_id)
    if data.suspended:
        core.run("UPDATE doctor_profiles SET online = 0 WHERE user_id = ?", user_id)
        await hub.send_all(drop_doctor(user_id))
        await hub.send_all(bookings.cancel_all_for_doctor(user_id))   # a suspended doctor's bookings are refunded in full
        # A suspended patient's search or waiting request is cancelled. A call already in progress is left to finish.
        for c in core.rows(f"SELECT * FROM consultations WHERE patient_id = ? AND status IN {LIVE_FOR_PATIENT} AND status != 'active'", user_id):
            await hub.send_all(release_unconnected(c, "cancelled", "system"))
    core.audit(admin["id"], "user_suspended" if data.suspended else "user_unsuspended", user_id, reason=data.reason)
    return {"id": user_id, "status": "suspended" if data.suspended else "active"}


class Adjust(BaseModel):
    user_id: str
    amount_taka: float = Field(ge=-50000, le=50000)
    reason: str = Field(min_length=2, max_length=300)
    kind: Literal["adjust", "payout"] = "adjust"   # payout = money sent out to a doctor (use a negative amount)


@route("/admin/wallet/adjust", ["POST"], roles=("admin",), admin_roles=SUPPORT, body=Adjust)
async def adjust_wallet(request, admin, data: Adjust):
    if not core.one("SELECT 1 FROM users WHERE id = ?", data.user_id):
        raise ApiError(404, "not_found", "User not found.")
    amount = core.to_poisha(data.amount_taka)
    if amount == 0 or (data.kind == "payout" and amount > 0):
        raise ApiError(422, "invalid_amount", "A payout must be a negative amount; an adjustment cannot be zero.")
    with core.tx():
        balance = wallet.add(data.user_id, data.kind, amount, "admin", admin["id"], data.reason)
        core.audit(admin["id"], f"wallet_{data.kind}", data.user_id, amount_taka=data.amount_taka, reason=data.reason)
    return {"user_id": data.user_id, "balance_taka": core.to_taka(balance)}


@route("/admin/bookings", ["GET"], roles=("admin",), admin_roles=SUPPORT)
async def list_bookings(request, admin, data):
    """Bookings for the support team: "upcoming" (default) or "past"."""
    if request.query_params.get("when", "upcoming") == "upcoming":
        found = core.rows("SELECT * FROM bookings WHERE status IN ('confirmed', 'in_call') ORDER BY start_at LIMIT 200")
    else:
        found = core.rows("SELECT * FROM bookings WHERE status NOT IN ('confirmed', 'in_call') ORDER BY start_at DESC LIMIT 200")
    return {"bookings": [{
        "id": b["id"], "status": b["status"], "start_at": b["start_at"], "end_at": b["end_at"], "problem": b["problem"],
        "price_taka": core.to_taka(b["price_poisha"]), "doctor_pay_taka": core.to_taka(b["doctor_pay_poisha"]),
        "refund_taka": core.to_taka(b["refund_poisha"]), "doctor_paid_taka": core.to_taka(b["doctor_paid_poisha"]),
        "cancelled_by": b["cancelled_by"], "patient": user_summary(b["patient_id"]), "doctor": user_summary(b["doctor_id"]),
    } for b in found]}


@route("/admin/reports", ["GET"], roles=("admin",), admin_roles=SUPPORT)
async def list_reports(request, admin, data):
    status = request.query_params.get("status", "open")
    found = core.rows("SELECT * FROM reports WHERE status = ? ORDER BY created_at ASC LIMIT 200", status)
    out = []
    for r in found:
        c = core.one("SELECT problem, started_at, duration_seconds, status FROM consultations WHERE id = ?", r["consultation_id"])
        out.append({**dict(r), "reporter": user_summary(r["reporter_id"]), "target": user_summary(r["target_id"]),
                    "consultation": dict(c) if c else None})
    return {"reports": out}


class Resolve(BaseModel):
    resolution: str = Field(min_length=2, max_length=500)


@route("/admin/reports/{report_id}/resolve", ["POST"], roles=("admin",), admin_roles=SUPPORT, body=Resolve)
async def resolve_report(request, admin, data: Resolve):
    changed = core.run("UPDATE reports SET status = 'resolved', resolution = ?, resolved_by = ?, resolved_at = ? WHERE id = ? AND status = 'open'",
                       data.resolution, admin["id"], core.now(), request.path_params["report_id"])
    if not changed:
        raise ApiError(404, "not_found", "Open report not found.")
    core.audit(admin["id"], "report_resolved", request.path_params["report_id"], resolution=data.resolution)
    return {"id": request.path_params["report_id"], "status": "resolved"}


@route("/admin/audit", ["GET"], roles=("admin",), admin_roles=SUPER)
async def audit_log(request, admin, data):
    found = core.rows("SELECT * FROM audit_log ORDER BY created_at DESC, rowid DESC LIMIT 200")
    return {"entries": [{**dict(r), "detail": json.loads(r["detail"])} for r in found]}


@route("/admin/dashboard", ["GET"], roles=("admin",))
async def dashboard(request, admin, data):
    day = core.bd_day_start(core.now())
    count = lambda sql, *a: core.one(sql, *a)["n"]
    money = core.one("SELECT COALESCE(SUM(price_charged_poisha), 0) AS income, COALESCE(SUM(doctor_pay_poisha), 0) AS pay "
                     "FROM consultations WHERE status = 'ended' AND ended_at >= ?", day)
    return {
        "doctors_pending": count("SELECT COUNT(*) AS n FROM doctor_profiles WHERE status = 'pending'"),
        "doctors_online": count("SELECT COUNT(*) AS n FROM doctor_profiles WHERE online = 1"),
        "calls_in_progress": count("SELECT COUNT(*) AS n FROM consultations WHERE status = 'active'"),
        "patients_waiting": count("SELECT COUNT(*) AS n FROM consultations WHERE status IN ('searching', 'offered', 'waiting', 'ready')"),
        "waiting_requests": count("SELECT COUNT(*) AS n FROM consultations WHERE status = 'waiting'"),
        "bookings_upcoming": count("SELECT COUNT(*) AS n FROM bookings WHERE status = 'confirmed'"),
        "open_reports": count("SELECT COUNT(*) AS n FROM reports WHERE status = 'open'"),
        "today": {"calls": count("SELECT COUNT(*) AS n FROM consultations WHERE status = 'ended' AND ended_at >= ?", day),
                  "income_taka": core.to_taka(money["income"]), "doctor_pay_taka": core.to_taka(money["pay"]),
                  "margin_taka": core.to_taka(money["income"] - money["pay"])},
    }
