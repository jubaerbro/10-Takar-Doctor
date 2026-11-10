"""The instant consult: queue, offer to a doctor, the call, the charge and the doctor's pay.

A consultation moves through these states:

    searching -> offered -> active -> ended      (charged, doctor paid)
                                   -> skipped    (ended within the free-skip window: no charge, no pay)

When no matching doctor is free, the request is parked instead of failing:

    searching -> waiting                         (no money held while waiting)
    waiting -> offered -> ready -> active        (a matching doctor came online and picked it up; the
                                                  patient then has a short time to join before it starts)
    ready -> waiting                             (patient did not join in time; two misses cancel the request)
    waiting -> no_doctor                         (nobody came online before the request expired)
    any state before active -> cancelled         (patient gave up)

"searching" only exists for an instant inside a request: it always becomes "offered" or "waiting".

Functions here change the database and return a list of (user_id, event, data) to push to the apps.
"""
from __future__ import annotations

import json
import math
from typing import Literal

import jwt
from pydantic import BaseModel, Field

from . import core, settings_store, wallet
from .accounts import doctor_public, patient_public
from .api import hub, route
from .core import ApiError

Events = list[tuple[str, str, dict]]
STALE_SECONDS = 90   # a doctor who has not sent a heartbeat for this long is treated as offline
NOTE_EDIT_SECONDS = 3600
MAX_MISSED_JOINS = 2  # a waiting request is cancelled after the patient fails to join this many times
LIVE_FOR_PATIENT = "('searching', 'offered', 'waiting', 'ready', 'active')"
LIVE_FOR_DOCTOR = "('offered', 'ready', 'active')"


# --------------------------------------------------------------------------- views


def video_credentials(c, user_id: str) -> dict | None:
    """Join details for the video room. Uses LiveKit's access-token format (a signed JWT)."""
    cfg = core.config
    if not (cfg.livekit_api_key and cfg.livekit_api_secret):
        return None
    payload = {
        "iss": cfg.livekit_api_key, "sub": user_id, "nbf": int(core.now()) - 10,
        "exp": int(c["started_at"] + c["max_seconds"]) + 60,
        "video": {"room": c["id"], "roomJoin": True, "canPublish": True, "canSubscribe": True},
    }
    return {"url": cfg.livekit_url, "room": c["id"], "token": jwt.encode(payload, cfg.livekit_api_secret, algorithm="HS256")}


def view(c, role: str) -> dict:
    """A consultation as one side is allowed to see it."""
    out = {
        "id": c["id"], "type": c["type"], "status": c["status"], "problem": c["problem"],
        "created_at": c["created_at"], "started_at": c["started_at"], "ended_at": c["ended_at"],
        "duration_seconds": c["duration_seconds"], "ended_by": c["ended_by"],
        "max_seconds": c["max_seconds"], "free_skip_seconds": c["free_skip_seconds"],
        "ends_at": c["started_at"] + c["max_seconds"] if c["status"] == "active" else None,
        "video": None,
        # waiting request details
        "from_waiting_request": c["waiting_since"] is not None,
        "waiting_since": c["waiting_since"],
        "expires_at": c["waiting_since"] + settings_store.get("waiting_request_minutes") * 60 if c["status"] == "waiting" else None,
        "join_by": c["ready_at"] + settings_store.get("patient_join_seconds") if c["status"] == "ready" else None,
    }
    note = core.one("SELECT assessment, self_care, tests, referral FROM advice_notes WHERE consultation_id = ?", c["id"])
    out["advice_note"] = dict(note) if note else None
    if role == "patient":
        out["price_taka"] = core.to_taka(c["hold_poisha"])
        out["price_charged_taka"] = core.to_taka(c["price_charged_poisha"])
        out["rating"] = c["rating"]
        show_doctor = c["doctor_id"] and c["status"] in ("ready", "active", "ended", "skipped")
        out["doctor"] = doctor_public(core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", c["doctor_id"])) if show_doctor else None
        if c["status"] == "active":
            out["video"] = video_credentials(c, c["patient_id"])
    else:
        out["patient"] = patient_public(c["patient_id"])
        out["doctor_pay_taka"] = core.to_taka(c["doctor_pay_poisha"])
        if c["status"] == "offered":
            out["offer_expires_at"] = c["offered_at"] + settings_store.get("offer_timeout_seconds")
        if c["status"] == "active":
            out["video"] = video_credentials(c, c["doctor_id"])
    return out


def load(consultation_id: str):
    c = core.one("SELECT * FROM consultations WHERE id = ?", consultation_id)
    if not c:
        raise ApiError(404, "not_found", "Consultation not found.")
    return c


def load_for(consultation_id: str, user: dict):
    c = load(consultation_id)
    owner = c["patient_id"] if user["role"] == "patient" else c["doctor_id"]
    if owner != user["id"]:
        raise ApiError(404, "not_found", "Consultation not found.")
    return c


def both(c_id: str) -> Events:
    """Tell both sides the consultation changed."""
    c = load(c_id)
    events: Events = [(c["patient_id"], "consultation", view(c, "patient"))]
    if c["doctor_id"]:
        events.append((c["doctor_id"], "consultation", view(c, "doctor")))
    return events


# --------------------------------------------------------------------------- matching


def find_doctor(c):
    """The free, online, verified doctor who matches the filters and has waited longest."""
    filters, declined = json.loads(c["filters"]), set(json.loads(c["declined"]))
    sql = (
        "SELECT d.* FROM doctor_profiles d JOIN users u ON u.id = d.user_id "
        "WHERE d.status = 'approved' AND u.status = 'active' AND d.online = 1 AND d.last_seen > ? "
        "AND d.user_id NOT IN (SELECT doctor_id FROM consultations "
        "                      WHERE status IN ('offered', 'ready', 'active') AND doctor_id IS NOT NULL)"
    )
    args: list = [core.now() - STALE_SECONDS]
    if filters.get("specialty"):
        sql += " AND d.specialty = ?"
        args.append(filters["specialty"])
    if filters.get("doctor_gender"):
        sql += " AND d.gender = ?"
        args.append(filters["doctor_gender"])
    sql += " ORDER BY d.last_assigned_at ASC, d.user_id ASC"
    for d in core.rows(sql, *args):
        if d["user_id"] in declined:
            continue
        if filters.get("language") and filters["language"] not in json.loads(d["languages"]):
            continue
        return d
    return None


def park(c) -> None:
    """No matching doctor is free: keep the request as a waiting request and hold no money meanwhile."""
    with core.tx():
        if c["held"]:
            wallet.add(c["patient_id"], "release", c["hold_poisha"], "consultation", c["id"], "Instant call: hold returned")
        core.run(
            "UPDATE consultations SET status = 'waiting', held = 0, doctor_id = NULL, offered_at = NULL, ready_at = NULL, "
            "waiting_since = COALESCE(waiting_since, ?) WHERE id = ?", core.now(), c["id"])


def try_assign(consultation_id: str) -> Events:
    """Offer the request to a free matching doctor, or park it as a waiting request if there is none."""
    with core.tx():
        c = load(consultation_id)
        if c["status"] not in ("searching", "waiting"):
            return []
        d = find_doctor(c)
        if not d:
            if c["status"] == "waiting":
                return []
            park(c)
            return both(consultation_id)
        now = core.now()
        if c["status"] == "searching" and not c["held"]:
            # The patient is on the searching screen right now: hold the price while the doctor is asked.
            wallet.add(c["patient_id"], "hold", -c["hold_poisha"], "consultation", c["id"], "Instant call: amount held")
            core.run("UPDATE consultations SET held = 1 WHERE id = ?", c["id"])
        core.run("UPDATE consultations SET status = 'offered', doctor_id = ?, offered_at = ? WHERE id = ?",
                 d["user_id"], now, c["id"])
        core.run("UPDATE doctor_profiles SET last_assigned_at = ? WHERE user_id = ?", now, d["user_id"])
    c = load(consultation_id)
    return [(c["doctor_id"], "offer", view(c, "doctor")), (c["patient_id"], "consultation", view(c, "patient"))]


def assign_pending() -> Events:
    """Give waiting requests, oldest first, to matching doctors who are free. Runs whenever a doctor may have become free."""
    events: Events = []
    for c in core.rows("SELECT id FROM consultations WHERE status IN ('searching', 'waiting') ORDER BY created_at ASC"):
        events += try_assign(c["id"])
    return events


def request_match(patient: dict, filters: dict, problem: str) -> tuple[str, Events]:
    if patient["consent_version"] != settings_store.get("terms_version"):
        raise ApiError(409, "consent_needed", "Please accept the terms first.")
    if not core.one("SELECT 1 FROM patient_profiles WHERE user_id = ?", patient["id"]):
        raise ApiError(409, "profile_missing", "Please complete your profile first.")
    if core.one(f"SELECT 1 FROM consultations WHERE patient_id = ? AND status IN {LIVE_FOR_PATIENT}", patient["id"]):
        raise ApiError(409, "already_in_call", "You already have a call or a waiting request. Cancel it to start a new search.")
    price = core.to_poisha(settings_store.get("instant_call_price_taka"))
    if patient["balance_poisha"] < price:
        raise ApiError(402, "insufficient_balance", "Your balance is too low. Please top up.")
    consultation_id = core.new_id()
    core.run(
        "INSERT INTO consultations (id, patient_id, status, problem, filters, hold_poisha, created_at) VALUES (?,?,?,?,?,?,?)",
        consultation_id, patient["id"], "searching", problem, json.dumps(filters), price, core.now(),
    )
    return consultation_id, try_assign(consultation_id)


def pass_offer(c, timed_out: bool) -> Events:
    """The offered doctor declined or did not answer: move the call to the next doctor."""
    doctor_id = c["doctor_id"]
    with core.tx():
        declined = json.loads(c["declined"]) + [doctor_id]
        # Money held means the patient is on the searching screen; otherwise this is a parked waiting request.
        core.run("UPDATE consultations SET status = ?, doctor_id = NULL, offered_at = NULL, declined = ? WHERE id = ?",
                 "searching" if c["held"] else "waiting", json.dumps(declined), c["id"])
        if timed_out:  # a doctor who lets an offer ring out is away from the phone
            core.run("UPDATE doctor_profiles SET online = 0 WHERE user_id = ?", doctor_id)
    events: Events = [(doctor_id, "offer_closed", {"id": c["id"], "set_offline": timed_out})]
    return events + (try_assign(c["id"]) or both(c["id"]))


def start_call(c) -> None:
    """The call begins now. Copy the current rules and pay rates onto it so later setting changes cannot alter it."""
    d = core.one("SELECT designation FROM doctor_profiles WHERE user_id = ?", c["doctor_id"])
    rates = settings_store.get("doctor_pay")[d["designation"]]
    core.run(
        "UPDATE consultations SET status = 'active', started_at = ?, max_seconds = ?, free_skip_seconds = ?, "
        "pay_per_call_poisha = ?, pay_per_minute_poisha = ?, designation = ? WHERE id = ?",
        core.now(), int(settings_store.get("instant_call_max_minutes") * 60), int(settings_store.get("free_skip_seconds")),
        core.to_poisha(rates["per_call_taka"]), core.to_poisha(rates["per_minute_taka"]), d["designation"], c["id"],
    )


def accept(doctor: dict, consultation_id: str) -> tuple[Events, bool]:
    """The doctor takes the offered call. Returns (events, still_available).

    A patient who is on the searching screen is connected at once. A waiting request first becomes "ready":
    the price is held and the patient is asked to join, so nobody is charged for a call they are not there for.
    """
    with core.tx():
        c = load(consultation_id)
        if c["doctor_id"] != doctor["id"] or c["status"] != "offered":
            raise ApiError(409, "offer_gone", "This call is no longer available.")
        if c["held"]:
            start_call(c)
            return both(consultation_id), True
        try:
            wallet.add(c["patient_id"], "hold", -c["hold_poisha"], "consultation", c["id"], "Instant call: amount held")
        except ApiError:
            # The patient spent their balance while waiting: the request cannot go ahead.
            return release_unconnected(c, "cancelled", "system"), False
        core.run("UPDATE consultations SET status = 'ready', held = 1, ready_at = ? WHERE id = ?", core.now(), c["id"])
    return both(consultation_id), True


def join(patient: dict, consultation_id: str) -> Events:
    """The patient answers a doctor who picked up their waiting request: the call starts."""
    with core.tx():
        c = load(consultation_id)
        if c["patient_id"] != patient["id"]:
            raise ApiError(404, "not_found", "Consultation not found.")
        if c["status"] != "ready":
            raise ApiError(409, "not_ready", "No doctor is waiting for you on this request right now.")
        start_call(c)
    return both(consultation_id)


def return_to_waiting(c, count_miss: bool) -> Events:
    """A ready request loses its doctor: the patient did not join in time, or the doctor left."""
    doctor_id = c["doctor_id"]
    with core.tx():
        if c["held"]:
            wallet.add(c["patient_id"], "release", c["hold_poisha"], "consultation", c["id"], "Instant call: hold returned")
        declined = json.dumps(json.loads(c["declined"]) + [doctor_id])
        missed = c["missed_joins"] + (1 if count_miss else 0)
        if missed >= MAX_MISSED_JOINS:
            core.run("UPDATE consultations SET status = 'cancelled', ended_by = 'system', ended_at = ?, held = 0, doctor_id = NULL, "
                     "ready_at = NULL, missed_joins = ?, declined = ? WHERE id = ?", core.now(), missed, declined, c["id"])
        else:
            core.run("UPDATE consultations SET status = 'waiting', held = 0, doctor_id = NULL, offered_at = NULL, ready_at = NULL, "
                     "missed_joins = ?, declined = ? WHERE id = ?", missed, declined, c["id"])
    return [(doctor_id, "offer_closed", {"id": c["id"], "set_offline": False})] + both(c["id"])


def drop_doctor(doctor_id: str) -> Events:
    """The doctor went offline or was suspended: hand back anything they were offered or had picked up."""
    events: Events = []
    for c in core.rows("SELECT * FROM consultations WHERE doctor_id = ? AND status IN ('offered', 'ready')", doctor_id):
        events += pass_offer(c, timed_out=False) if c["status"] == "offered" else return_to_waiting(c, count_miss=False)
    return events


def release_unconnected(c, status: str, ended_by: str) -> Events:
    """End a consultation that never connected and give back any held money."""
    doctor_id = c["doctor_id"]
    with core.tx():
        if c["held"]:
            wallet.add(c["patient_id"], "release", c["hold_poisha"], "consultation", c["id"], "Instant call: hold returned")
        core.run("UPDATE consultations SET status = ?, ended_by = ?, ended_at = ?, held = 0, doctor_id = NULL, ready_at = NULL WHERE id = ?",
                 status, ended_by, core.now(), c["id"])
    events = both(c["id"])
    if doctor_id:
        events.append((doctor_id, "offer_closed", {"id": c["id"], "set_offline": False}))
    return events


def finish(c, ended_by: str) -> Events:
    """End a connected call, then charge the patient and pay the doctor, or neither if it was a free skip."""
    now = core.now()
    elapsed = max(0.0, now - c["started_at"])
    duration = int(min(elapsed, c["max_seconds"]))
    free = elapsed <= c["free_skip_seconds"]
    if free and ended_by == "patient":
        used = core.one(
            "SELECT COUNT(*) AS n FROM consultations WHERE patient_id = ? AND status = 'skipped' AND ended_by = 'patient' AND ended_at >= ?",
            c["patient_id"], core.bd_day_start(now))["n"]
        free = used < settings_store.get("free_skips_per_day")
    with core.tx():
        wallet.add(c["patient_id"], "release", c["hold_poisha"], "consultation", c["id"], "Instant call: hold returned")
        price = pay = 0
        if not free:
            price = c["hold_poisha"]
            minutes = max(1, math.ceil(duration / 60))
            pay = c["pay_per_call_poisha"] + c["pay_per_minute_poisha"] * minutes
            wallet.add(c["patient_id"], "charge", -price, "consultation", c["id"], "Instant call")
            wallet.add(c["doctor_id"], "earning", pay, "consultation", c["id"], f"Instant call, {minutes} min")
        core.run(
            "UPDATE consultations SET status = ?, ended_by = ?, ended_at = ?, duration_seconds = ?, held = 0, "
            "price_charged_poisha = ?, doctor_pay_poisha = ? WHERE id = ?",
            "skipped" if free else "ended", ended_by, now, duration, price, pay, c["id"],
        )
    return both(c["id"])


def sweep() -> Events:
    """Housekeeping, run every second: time out offers and joins, cut off long calls, expire old requests, fill free doctors."""
    now = core.now()
    events: Events = []
    core.run("UPDATE doctor_profiles SET online = 0 WHERE online = 1 AND last_seen <= ?", now - STALE_SECONDS)
    for c in core.rows("SELECT * FROM consultations WHERE status = 'offered' AND offered_at <= ?",
                       now - settings_store.get("offer_timeout_seconds")):
        events += pass_offer(c, timed_out=True)
    for c in core.rows("SELECT * FROM consultations WHERE status = 'active' AND started_at + max_seconds <= ?", now):
        events += finish(c, "system")
    for c in core.rows("SELECT * FROM consultations WHERE status = 'ready' AND ready_at <= ?",
                       now - settings_store.get("patient_join_seconds")):
        events += return_to_waiting(c, count_miss=True)
    for c in core.rows("SELECT * FROM consultations WHERE status = 'waiting' AND waiting_since <= ?",
                       now - settings_store.get("waiting_request_minutes") * 60):
        events += release_unconnected(c, "no_doctor", "system")
    return events + assign_pending()


async def tick() -> None:
    await hub.send_all(sweep())


# --------------------------------------------------------------------------- public config


@route("/config", ["GET"])
async def public_config(request, user, data):
    """What the app needs to show prices and choices before the user taps anything."""
    s = settings_store.all_settings()
    return {
        "instant_call_price_taka": s["instant_call_price_taka"], "instant_call_max_minutes": s["instant_call_max_minutes"],
        "free_skip_seconds": s["free_skip_seconds"], "min_topup_taka": s["min_topup_taka"],
        "doctor_registration_fee_taka": s["doctor_registration_fee_taka"], "terms_version": s["terms_version"],
        "specialties": s["specialties"], "designations": settings_store.DESIGNATIONS,
        "test_mode": core.config.dev_mode,   # the apps show a TEST banner when this is on
    }


# --------------------------------------------------------------------------- doctor availability


def approved_doctor(user: dict):
    d = core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", user["id"])
    if not d or d["status"] != "approved":
        raise ApiError(403, "not_verified", "Your account is not verified yet.")
    return d


@route("/doctor/online", ["POST"], roles=("doctor",))
async def go_online(request, user, data):
    approved_doctor(user)
    if user["consent_version"] != settings_store.get("terms_version"):
        raise ApiError(409, "consent_needed", "Please accept the latest doctor agreement first.")
    now = core.now()
    # Coming online joins the back of the line; tapping "online" again while online keeps your place.
    core.run("UPDATE doctor_profiles SET last_assigned_at = ? WHERE user_id = ? AND online = 0", now, user["id"])
    core.run("UPDATE doctor_profiles SET online = 1, last_seen = ? WHERE user_id = ?", now, user["id"])
    await tick()
    return {"online": True}


@route("/doctor/heartbeat", ["POST"], roles=("doctor",))
async def heartbeat(request, user, data):
    """The doctor app calls this every 30 seconds while online."""
    core.run("UPDATE doctor_profiles SET last_seen = ? WHERE user_id = ?", core.now(), user["id"])
    d = core.one("SELECT online FROM doctor_profiles WHERE user_id = ?", user["id"])
    return {"online": bool(d and d["online"])}


@route("/doctor/offline", ["POST"], roles=("doctor",))
async def go_offline(request, user, data):
    core.run("UPDATE doctor_profiles SET online = 0 WHERE user_id = ?", user["id"])
    await hub.send_all(drop_doctor(user["id"]))
    return {"online": False}


@route("/doctor/earnings", ["GET"], roles=("doctor",))
async def earnings(request, user, data):
    now = core.now()

    def total(since: float) -> dict:
        row = core.one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(doctor_pay_poisha), 0) AS pay FROM consultations "
            "WHERE doctor_id = ? AND status = 'ended' AND ended_at >= ?", user["id"], since)
        return {"calls": row["calls"], "earned_taka": core.to_taka(row["pay"])}

    return {"balance_taka": core.to_taka(user["balance_poisha"]), "today": total(core.bd_day_start(now)),
            "last_7_days": total(now - 7 * 86400), "all_time": total(0)}


# --------------------------------------------------------------------------- the call


class MatchRequest(BaseModel):
    problem: str = Field(min_length=2, max_length=300)
    specialty: str | None = None
    language: Literal["bn", "en"] | None = None
    doctor_gender: Literal["male", "female"] | None = None


@route("/match", ["POST"], roles=("patient",), body=MatchRequest)
async def start_match(request, user, data: MatchRequest):
    if data.specialty and data.specialty not in settings_store.get("specialties"):
        raise ApiError(422, "invalid_specialty", "Choose a specialty from the list.")
    filters = {k: v for k, v in data.model_dump(exclude={"problem"}).items() if v}
    consultation_id, events = request_match(user, filters, data.problem.strip())
    await hub.send_all(events)
    return view(load(consultation_id), "patient")


@route("/consultations/current", ["GET"], roles=("patient", "doctor"))
async def current(request, user, data):
    """The call in progress, if any. Apps poll this (or listen on /ws) while searching or waiting for offers."""
    await tick()
    column = "patient_id" if user["role"] == "patient" else "doctor_id"
    states = LIVE_FOR_PATIENT if user["role"] == "patient" else LIVE_FOR_DOCTOR
    c = core.one(f"SELECT * FROM consultations WHERE {column} = ? AND status IN {states}", user["id"])
    return {"consultation": view(c, user["role"]) if c else None}


@route("/consultations", ["GET"], roles=("patient", "doctor"))
async def history(request, user, data):
    column = "patient_id" if user["role"] == "patient" else "doctor_id"
    found = core.rows(
        f"SELECT * FROM consultations WHERE {column} = ? AND status IN ('ended', 'skipped') ORDER BY created_at DESC LIMIT 50",
        user["id"])
    return {"consultations": [view(c, user["role"]) for c in found]}


@route("/consultations/{consultation_id}", ["GET"], roles=("patient", "doctor"))
async def get_consultation(request, user, data):
    await tick()
    return view(load_for(request.path_params["consultation_id"], user), user["role"])


@route("/consultations/{consultation_id}/accept", ["POST"], roles=("doctor",))
async def accept_offer(request, user, data):
    await tick()
    events, still_available = accept(user, request.path_params["consultation_id"])
    await hub.send_all(events)
    if not still_available:
        raise ApiError(409, "offer_gone", "This call is no longer available.")
    return view(load(request.path_params["consultation_id"]), "doctor")


@route("/consultations/{consultation_id}/join", ["POST"], roles=("patient",))
async def join_call(request, user, data):
    """A doctor picked up the patient's waiting request (status "ready"): the patient joins and the call starts."""
    await tick()
    await hub.send_all(join(user, request.path_params["consultation_id"]))
    return view(load(request.path_params["consultation_id"]), "patient")


@route("/consultations/{consultation_id}/decline", ["POST"], roles=("doctor",))
async def decline_offer(request, user, data):
    c = load_for(request.path_params["consultation_id"], user)
    if c["status"] != "offered":
        raise ApiError(409, "offer_gone", "This call is no longer available.")
    await hub.send_all(pass_offer(c, timed_out=False))
    return {"declined": True}


@route("/consultations/{consultation_id}/cancel", ["POST"], roles=("patient",))
async def cancel_search(request, user, data):
    c = load_for(request.path_params["consultation_id"], user)
    if c["status"] not in ("searching", "offered", "waiting", "ready"):
        raise ApiError(409, "cannot_cancel", "This call has already started or finished.")
    await hub.send_all(release_unconnected(c, "cancelled", "patient"))
    return view(load(c["id"]), "patient")


@route("/consultations/{consultation_id}/end", ["POST"], roles=("patient", "doctor"))
async def end_call(request, user, data):
    """End the call. For a patient this is also the 'next doctor' button: end, then POST /match again."""
    await tick()
    c = load_for(request.path_params["consultation_id"], user)
    if c["status"] != "active":
        raise ApiError(409, "not_active", "This call is not in progress.")
    await hub.send_all(finish(c, user["role"]))
    await tick()
    return view(load(c["id"]), user["role"])


# --------------------------------------------------------------------------- after the call


class AdviceNote(BaseModel):
    assessment: str = Field(min_length=2, max_length=1000)
    self_care: str = Field(default="", max_length=1000)
    tests: str = Field(default="", max_length=1000)
    referral: str = Field(default="", max_length=1000)


@route("/consultations/{consultation_id}/advice-note", ["PUT"], roles=("doctor",), body=AdviceNote)
async def write_advice_note(request, user, data: AdviceNote):
    """Advice only: there is deliberately no field for medicines."""
    c = load_for(request.path_params["consultation_id"], user)
    if c["status"] != "ended":
        raise ApiError(409, "not_ended", "You can write the note once the call has ended.")
    if core.now() - c["ended_at"] > NOTE_EDIT_SECONDS:
        raise ApiError(409, "note_locked", "The note can no longer be changed.")
    core.run(
        "INSERT INTO advice_notes (consultation_id, assessment, self_care, tests, referral, created_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT (consultation_id) DO UPDATE SET assessment = excluded.assessment, self_care = excluded.self_care, "
        "tests = excluded.tests, referral = excluded.referral",
        c["id"], data.assessment, data.self_care, data.tests, data.referral, core.now(),
    )
    await hub.send_all(both(c["id"]))
    return view(load(c["id"]), "doctor")


class Rating(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


@route("/consultations/{consultation_id}/rating", ["POST"], roles=("patient",), body=Rating)
async def rate(request, user, data: Rating):
    c = load_for(request.path_params["consultation_id"], user)
    if c["status"] != "ended":
        raise ApiError(409, "not_ended", "You can rate a call once it has ended.")
    if c["rating"] is not None:
        raise ApiError(409, "already_rated", "You have already rated this call.")
    with core.tx():
        core.run("UPDATE consultations SET rating = ?, rating_comment = ? WHERE id = ?", data.rating, data.comment, c["id"])
        core.run("UPDATE doctor_profiles SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE user_id = ?",
                 data.rating, c["doctor_id"])
    return view(load(c["id"]), "patient")


class Report(BaseModel):
    reason: Literal["abuse", "prescribed_medicine", "inappropriate", "fake_doctor", "other"]
    details: str = Field(default="", max_length=1000)


@route("/consultations/{consultation_id}/report", ["POST"], roles=("patient", "doctor"), body=Report)
async def report(request, user, data: Report):
    c = load_for(request.path_params["consultation_id"], user)
    if c["status"] not in ("active", "ended", "skipped"):
        raise ApiError(409, "not_connected", "You can only report a call that connected.")
    target = c["doctor_id"] if user["role"] == "patient" else c["patient_id"]
    report_id = core.new_id()
    core.run("INSERT INTO reports (id, consultation_id, reporter_id, target_id, reason, details, created_at) VALUES (?,?,?,?,?,?,?)",
             report_id, c["id"], user["id"], target, data.reason, data.details, core.now())
    return {"id": report_id, "status": "open"}
