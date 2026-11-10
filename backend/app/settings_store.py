"""Admin-editable settings. Every price, pay rate, fee and limit lives here, not in the code."""
from __future__ import annotations

import json

from . import core
from .core import ApiError

# Pay tiers, highest first. Admin assigns one to each doctor at approval.
DESIGNATIONS = ["professor", "associate_professor", "assistant_professor", "consultant", "medical_officer"]

# Starting values. The pay and booking figures are PLACEHOLDERS for the admin to replace.
DEFAULTS: dict = {
    "instant_call_price_taka": 10,       # charge per connected random match
    "instant_call_max_minutes": 10,      # the call is cut off at this length
    "free_skip_seconds": 3,              # a call ended within this time is free
    "free_skips_per_day": 10,            # per patient; after this, quick skips are charged
    "offer_timeout_seconds": 15,         # how long a doctor has to accept before the call moves on
    "waiting_request_minutes": 360,      # how long a request waits for a matching doctor to come online
    "patient_join_seconds": 60,          # time the patient has to join once a doctor picks up their waiting request
    "min_topup_taka": 20,
    "doctor_registration_fee_taka": 0,   # charged from the doctor's wallet when they submit for verification
    "terms_version": "1",                # bump to make every user accept the terms again
    "doctor_pay": {                      # per call (main part) plus a smaller per-minute part, by designation
        "professor":           {"per_call_taka": 20, "per_minute_taka": 2},
        "associate_professor": {"per_call_taka": 16, "per_minute_taka": 1.5},
        "assistant_professor": {"per_call_taka": 12, "per_minute_taka": 1},
        "consultant":          {"per_call_taka": 8,  "per_minute_taka": 0.8},
        "medical_officer":     {"per_call_taka": 5,  "per_minute_taka": 0.5},
    },
    "booking_price_taka": {              # price to book a known doctor, by designation (bookings come later)
        "professor": 500, "associate_professor": 400, "assistant_professor": 300,
        "consultant": 200, "medical_officer": 100,
    },
    "specialties": ["general", "child", "skin", "womens_health", "mental_health", "heart", "diabetes", "ent", "eye", "dental"],
}


def _number(key: str, value, minimum: float = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ApiError(422, "invalid_setting", f"{key} must be a number of at least {minimum}.")


def validate(key: str, value) -> None:
    if key not in DEFAULTS:
        raise ApiError(422, "unknown_setting", f"There is no setting called {key}.")
    if key == "doctor_pay":
        if not isinstance(value, dict) or set(value) != set(DESIGNATIONS):
            raise ApiError(422, "invalid_setting", "doctor_pay needs exactly one entry per designation.")
        for tier, rates in value.items():
            if not isinstance(rates, dict) or set(rates) != {"per_call_taka", "per_minute_taka"}:
                raise ApiError(422, "invalid_setting", f"doctor_pay.{tier} needs per_call_taka and per_minute_taka.")
            for name, amount in rates.items():
                _number(f"doctor_pay.{tier}.{name}", amount)
    elif key == "booking_price_taka":
        if not isinstance(value, dict) or set(value) != set(DESIGNATIONS):
            raise ApiError(422, "invalid_setting", "booking_price_taka needs exactly one entry per designation.")
        for tier, amount in value.items():
            _number(f"booking_price_taka.{tier}", amount)
    elif key == "specialties":
        if not isinstance(value, list) or not value or not all(isinstance(s, str) and s for s in value):
            raise ApiError(422, "invalid_setting", "specialties must be a non-empty list of names.")
    elif key == "terms_version":
        if not isinstance(value, str) or not value:
            raise ApiError(422, "invalid_setting", "terms_version must be text.")
    elif key in ("instant_call_max_minutes", "offer_timeout_seconds", "waiting_request_minutes", "patient_join_seconds"):
        _number(key, value, minimum=1)
    else:
        _number(key, value)


def get(key: str):
    row = core.one("SELECT value FROM settings WHERE key = ?", key)
    return json.loads(row["value"]) if row else DEFAULTS[key]


def all_settings() -> dict:
    stored = {r["key"]: json.loads(r["value"]) for r in core.rows("SELECT key, value FROM settings")}
    return {key: stored.get(key, default) for key, default in DEFAULTS.items()}


def update(changes: dict, admin_id: str) -> dict:
    """Validate and save several settings at once. Each change is written to the audit log."""
    for key, value in changes.items():
        validate(key, value)
    with core.tx():
        for key, value in changes.items():
            old = get(key)
            if old == value:
                continue
            core.run(
                "INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
                "updated_at = excluded.updated_at",
                key, json.dumps(value), admin_id, core.now(),
            )
            core.audit(admin_id, "setting_changed", key, old=old, new=value)
    return all_settings()
