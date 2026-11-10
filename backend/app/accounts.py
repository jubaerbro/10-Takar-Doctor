"""Login by phone OTP, patient and doctor profiles, consent, and doctor verification documents."""
from __future__ import annotations

import hmac
import json
import re
import secrets
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import core, settings_store, wallet
from .api import route
from .core import ApiError

PHONE_RE = re.compile(r"^(?:\+?88)?(01[3-9]\d{8})$")
OTP_TTL = 300            # seconds an OTP stays valid
OTP_MAX_ATTEMPTS = 5
OTP_PER_HOUR = 5
OTP_GAP_SECONDS = 60

DOC_TYPES = ("bmdc_certificate", "degree_certificate", "nid", "selfie")
REQUIRED_DOCS = DOC_TYPES  # at least one of each before a doctor can submit
ALLOWED_UPLOADS = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
FILE_SIGNATURES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG", "application/pdf": b"%PDF"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def normalize_phone(raw: str) -> str:
    match = PHONE_RE.match(re.sub(r"[\s-]", "", raw))
    if not match:
        raise ApiError(422, "invalid_phone", "Enter a valid Bangladeshi mobile number, like 01712345678.")
    return "+88" + match.group(1)


def send_sms(phone: str, text: str) -> None:
    """Deliver an SMS. Replace the body with your SMS provider's API call before launch."""
    if core.config.dev_mode:
        print(f"[dev sms] {phone}: {text}")
        return
    raise ApiError(503, "sms_not_configured", "SMS sending is not set up yet.")


# --------------------------------------------------------------------------- OTP login


class OtpRequest(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    code: str = Field(min_length=6, max_length=6)
    role: Literal["patient", "doctor"]
    language: Literal["bn", "en"] = "bn"


@route("/auth/otp/request", ["POST"], body=OtpRequest)
async def otp_request(request, user, data: OtpRequest):
    phone = normalize_phone(data.phone)
    now = core.now()
    recent = core.rows("SELECT created_at FROM otp_codes WHERE phone = ? AND created_at > ?", phone, now - 3600)
    if any(now - r["created_at"] < OTP_GAP_SECONDS for r in recent):
        raise ApiError(429, "otp_too_soon", "Please wait a minute before asking for another code.")
    if len(recent) >= OTP_PER_HOUR:
        raise ApiError(429, "otp_limit", "Too many codes requested. Try again in an hour.")
    code = core.new_otp()
    send_sms(phone, f"Your 10 Takar Doctor code is {code}. It expires in 5 minutes.")
    core.run(
        "INSERT INTO otp_codes (phone, code_hash, expires_at, created_at) VALUES (?,?,?,?)",
        phone, core.hash_otp(phone, code), now + OTP_TTL, now,
    )
    result = {"sent": True, "expires_in": OTP_TTL}
    if core.config.dev_mode:
        result["dev_code"] = code
    return result


@route("/auth/otp/verify", ["POST"], body=OtpVerify)
async def otp_verify(request, user, data: OtpVerify):
    phone = normalize_phone(data.phone)
    now = core.now()
    otp = core.one("SELECT rowid, * FROM otp_codes WHERE phone = ? ORDER BY created_at DESC LIMIT 1", phone)
    if not otp or otp["expires_at"] < now:
        raise ApiError(400, "otp_expired", "This code has expired. Ask for a new one.")
    if otp["attempts"] >= OTP_MAX_ATTEMPTS:
        raise ApiError(429, "otp_locked", "Too many wrong tries. Ask for a new code.")
    if not hmac.compare_digest(otp["code_hash"], core.hash_otp(phone, data.code)):
        core.run("UPDATE otp_codes SET attempts = attempts + 1 WHERE rowid = ?", otp["rowid"])
        raise ApiError(400, "otp_wrong", "That code is not correct.")
    core.run("DELETE FROM otp_codes WHERE phone = ?", phone)

    row = core.one("SELECT * FROM users WHERE phone = ? AND role = ?", phone, data.role)
    is_new = row is None
    if is_new:
        user_id = core.new_id()
        prefix = "Patient" if data.role == "patient" else "Doctor"
        core.run(
            "INSERT INTO users (id, phone, role, alias, language, created_at) VALUES (?,?,?,?,?,?)",
            user_id, phone, data.role, f"{prefix}-{secrets.randbelow(900000) + 100000}", data.language, now,
        )
        row = core.one("SELECT * FROM users WHERE id = ?", user_id)
    if row["status"] != "active":
        raise ApiError(403, "suspended", "This account is suspended. Contact support.")
    return {"token": core.make_token(row["id"], row["role"]), "is_new": is_new, "me": me_view(dict(row))}


# --------------------------------------------------------------------------- views


def doctor_public(d) -> dict:
    """What a patient is allowed to see about a doctor."""
    count = d["rating_count"]
    return {
        "id": d["user_id"],
        "name": d["real_name"],
        "bmdc_number": d["bmdc_number"],
        "specialty": d["specialty"],
        "designation": d["designation"],
        "gender": d["gender"],
        "experience_years": d["experience_years"],
        "languages": json.loads(d["languages"]),
        "rating": round(d["rating_sum"] / count, 1) if count else None,
        "rating_count": count,
        "verified": d["status"] == "approved",
    }


def patient_public(user_id: str) -> dict:
    """What a doctor is allowed to see about a patient: alias, age and gender. Never name or phone."""
    row = core.one(
        "SELECT u.alias, p.age, p.gender FROM users u JOIN patient_profiles p ON p.user_id = u.id WHERE u.id = ?",
        user_id,
    )
    return {"alias": row["alias"], "age": row["age"], "gender": row["gender"]}


def me_view(user: dict) -> dict:
    terms = settings_store.get("terms_version")
    view = {
        "id": user["id"], "role": user["role"], "phone": user["phone"], "alias": user["alias"],
        "language": user["language"], "balance_taka": core.to_taka(user["balance_poisha"]),
        "consent_needed": user["consent_version"] != terms, "terms_version": terms, "profile": None,
    }
    if user["role"] == "patient":
        p = core.one("SELECT * FROM patient_profiles WHERE user_id = ?", user["id"])
        if p:
            view["profile"] = {k: p[k] for k in ("real_name", "age", "gender", "district", "guardian_name", "guardian_relation")}
    else:
        d = core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", user["id"])
        if d:
            docs = core.rows("SELECT id, doc_type, created_at FROM doctor_documents WHERE doctor_id = ?", user["id"])
            view["profile"] = {
                **doctor_public(d), "status": d["status"], "review_reason": d["review_reason"],
                "online": bool(d["online"]), "documents": [dict(x) for x in docs],
            }
    return view


@route("/me", ["GET"], roles=("patient", "doctor"))
async def me(request, user, data):
    return me_view(user)


class Consent(BaseModel):
    version: str


@route("/me/consent", ["POST"], roles=("patient", "doctor"), body=Consent)
async def accept_terms(request, user, data: Consent):
    if data.version != settings_store.get("terms_version"):
        raise ApiError(409, "terms_changed", "The terms have changed. Please read the latest version.")
    core.run("UPDATE users SET consent_version = ?, consent_at = ? WHERE id = ?", data.version, core.now(), user["id"])
    return {"accepted": True}


# --------------------------------------------------------------------------- patient profile


class PatientProfile(BaseModel):
    real_name: str = Field(min_length=2, max_length=80)
    age: int = Field(ge=0, le=120)
    gender: Literal["male", "female", "other"]
    district: str = Field(min_length=2, max_length=60)
    guardian_name: str | None = Field(default=None, max_length=80)
    guardian_relation: str | None = Field(default=None, max_length=40)


@route("/patient/profile", ["PUT"], roles=("patient",), body=PatientProfile)
async def save_patient_profile(request, user, data: PatientProfile):
    if data.age < 18 and not (data.guardian_name and data.guardian_relation):
        raise ApiError(422, "guardian_required", "Patients under 18 need a guardian's name and relation.")
    core.run(
        "INSERT INTO patient_profiles (user_id, real_name, age, gender, district, guardian_name, guardian_relation, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (user_id) DO UPDATE SET real_name = excluded.real_name, age = excluded.age, "
        "gender = excluded.gender, district = excluded.district, guardian_name = excluded.guardian_name, "
        "guardian_relation = excluded.guardian_relation, updated_at = excluded.updated_at",
        user["id"], data.real_name, data.age, data.gender, data.district, data.guardian_name, data.guardian_relation, core.now(),
    )
    return me_view(user)


# --------------------------------------------------------------------------- doctor profile and verification


class DoctorProfile(BaseModel):
    real_name: str = Field(min_length=2, max_length=80)
    bmdc_number: str = Field(min_length=2, max_length=20)
    gender: Literal["male", "female", "other"]
    specialty: str
    experience_years: int = Field(ge=0, le=70)
    languages: list[Literal["bn", "en"]] = Field(min_length=1)


def editable_doctor(user_id: str):
    d = core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", user_id)
    if d and d["status"] in ("pending", "approved"):
        raise ApiError(409, "profile_locked", "Your details are locked once sent for verification. Contact support to change them.")
    return d


@route("/doctor/profile", ["PUT"], roles=("doctor",), body=DoctorProfile)
async def save_doctor_profile(request, user, data: DoctorProfile):
    editable_doctor(user["id"])
    if data.specialty not in settings_store.get("specialties"):
        raise ApiError(422, "invalid_specialty", "Choose a specialty from the list.")
    core.run(
        "INSERT INTO doctor_profiles (user_id, real_name, bmdc_number, gender, specialty, experience_years, languages, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (user_id) DO UPDATE SET real_name = excluded.real_name, "
        "bmdc_number = excluded.bmdc_number, gender = excluded.gender, specialty = excluded.specialty, "
        "experience_years = excluded.experience_years, languages = excluded.languages, updated_at = excluded.updated_at",
        user["id"], data.real_name, data.bmdc_number.strip(), data.gender, data.specialty, data.experience_years,
        json.dumps(sorted(set(data.languages))), core.now(),
    )
    return me_view(user)


@route("/doctor/documents", ["POST"], roles=("doctor",))
async def upload_document(request, user, data):
    """Multipart upload with fields: doc_type and file (JPEG, PNG or PDF, up to 8 MB)."""
    if not editable_doctor(user["id"]):
        raise ApiError(409, "profile_missing", "Fill in your profile before uploading documents.")
    form = await request.form()
    doc_type, upload = form.get("doc_type"), form.get("file")
    if doc_type not in DOC_TYPES:
        raise ApiError(422, "invalid_doc_type", f"doc_type must be one of: {', '.join(DOC_TYPES)}.")
    if upload is None or isinstance(upload, str):
        raise ApiError(422, "file_missing", "Attach the document as 'file'.")
    extension = ALLOWED_UPLOADS.get(upload.content_type)
    if not extension:
        raise ApiError(422, "invalid_file_type", "Upload a JPEG, PNG or PDF.")
    content = await upload.read()
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ApiError(422, "invalid_file_size", "The file must be under 8 MB.")
    if not content.startswith(FILE_SIGNATURES[upload.content_type]):   # the file must really be what it claims
        raise ApiError(422, "invalid_file_type", "Upload a JPEG, PNG or PDF.")
    doc_id = core.new_id()
    folder = Path(core.config.upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}{extension}").write_bytes(content)
    core.run(
        "INSERT INTO doctor_documents (id, doctor_id, doc_type, file_name, content_type, created_at) VALUES (?,?,?,?,?,?)",
        doc_id, user["id"], doc_type, f"{doc_id}{extension}", upload.content_type, core.now(),
    )
    return {"id": doc_id, "doc_type": doc_type}


@route("/doctor/submit", ["POST"], roles=("doctor",))
async def submit_for_verification(request, user, data):
    d = editable_doctor(user["id"])
    if not d:
        raise ApiError(409, "profile_missing", "Fill in your profile first.")
    if user["consent_version"] != settings_store.get("terms_version"):
        raise ApiError(409, "consent_needed", "Accept the doctor agreement first.")
    have = {r["doc_type"] for r in core.rows("SELECT doc_type FROM doctor_documents WHERE doctor_id = ?", user["id"])}
    missing = [t for t in REQUIRED_DOCS if t not in have]
    if missing:
        raise ApiError(409, "documents_missing", f"Still needed: {', '.join(missing)}.")
    fee = core.to_poisha(settings_store.get("doctor_registration_fee_taka"))
    with core.tx():
        # The fee is taken once: a doctor asked to re-upload does not pay again.
        paid = core.one("SELECT 1 FROM wallet_transactions WHERE user_id = ? AND type = 'fee'", user["id"])
        if fee > 0 and not paid:
            wallet.add(user["id"], "fee", -fee, "doctor_registration", user["id"], "Registration fee")
        core.run("UPDATE doctor_profiles SET status = 'pending', review_reason = NULL, updated_at = ? WHERE user_id = ?",
                 core.now(), user["id"])
    return me_view(dict(core.one("SELECT * FROM users WHERE id = ?", user["id"])))
