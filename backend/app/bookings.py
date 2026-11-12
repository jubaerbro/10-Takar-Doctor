"""Bookings: a patient books a doctor they have already consulted, for a fixed time, at the designation's price.

    doctor opens slots -> patient books one (price charged now) -> both join at the time -> booked call
    -> doctor is paid their share when the call ends

Rules (all numbers are admin settings):
  * A patient can only book a doctor they have had a completed call with.
  * The patient pays the full booking price when booking.
  * Patient cancels early enough: full refund and the slot reopens. Cancels late: no refund, doctor is paid.
  * Doctor cancels: full refund.
  * At the start time each side taps Join; the call starts when both have. It ends at the slot's end time.
  * If one side has not joined a few minutes after the start: doctor missing = full refund;
    patient missing = no refund and the doctor is paid; both missing = full refund.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from . import core, matching, settings_store, wallet
from .accounts import doctor_public, patient_public, send_sms
from .api import hub, route
from .core import ApiError
from .matching import Events, LIVE_FOR_DOCTOR, LIVE_FOR_PATIENT

JOIN_EARLY_SECONDS = 120      # Join opens this long before the start time
MIN_LEAD_SECONDS = 300        # a slot must start at least this far in the future to be opened or booked
MAX_AHEAD_DAYS = 30


# --------------------------------------------------------------------------- views


def slot_view(s) -> dict:
    return {"id": s["id"], "start_at": s["start_at"], "end_at": s["end_at"], "status": s["status"]}


def booking_view(b, role: str) -> dict:
    mine, theirs = ("patient_joined_at", "doctor_joined_at") if role == "patient" else ("doctor_joined_at", "patient_joined_at")
    out = {
        "id": b["id"], "status": b["status"], "start_at": b["start_at"], "end_at": b["end_at"], "problem": b["problem"],
        "join_from": b["start_at"] - JOIN_EARLY_SECONDS, "i_joined": b[mine] is not None, "other_joined": b[theirs] is not None,
        "consultation_id": b["consultation_id"], "cancelled_by": b["cancelled_by"],
    }
    if role == "patient":
        out["doctor"] = doctor_public(core.one("SELECT * FROM doctor_profiles WHERE user_id = ?", b["doctor_id"]))
        out["price_taka"] = core.to_taka(b["price_poisha"])
        out["refund_taka"] = core.to_taka(b["refund_poisha"])
        out["free_cancel_until"] = b["start_at"] - settings_store.get("booking_cancel_free_minutes") * 60
    else:
        out["patient"] = patient_public(b["patient_id"])
        out["pay_taka"] = core.to_taka(b["doctor_pay_poisha"])
        out["paid_taka"] = core.to_taka(b["doctor_paid_poisha"])
    return out


def notify(b) -> Events:
    b = core.one("SELECT * FROM bookings WHERE id = ?", b["id"])
    return [(b["patient_id"], "booking", booking_view(b, "patient")), (b["doctor_id"], "booking", booking_view(b, "doctor"))]


def load_for(booking_id: str, user: dict):
    b = core.one("SELECT * FROM bookings WHERE id = ?", booking_id)
    if not b or b["patient_id" if user["role"] == "patient" else "doctor_id"] != user["id"]:
        raise ApiError(404, "not_found", "Booking not found.")
    return b


# --------------------------------------------------------------------------- doctor's bookable hours


class NewSlots(BaseModel):
    starts: list[float] = Field(min_length=1, max_length=50)   # Unix times; each slot lasts booking_minutes


@route("/doctor/slots", ["GET"], roles=("doctor",))
async def my_slots(request, user, data):
    found = core.rows("SELECT * FROM availability_slots WHERE doctor_id = ? AND end_at > ? AND status != 'cancelled' ORDER BY start_at",
                      user["id"], core.now())
    return {"slots": [slot_view(s) for s in found], "minutes": settings_store.get("booking_minutes")}


@route("/doctor/slots", ["POST"], roles=("doctor",), body=NewSlots)
async def open_slots(request, user, data: NewSlots):
    matching.approved_doctor(user)
    now, length = core.now(), settings_store.get("booking_minutes") * 60
    created = []
    with core.tx():
        for start in sorted(set(data.starts)):
            end = start + length
            if start < now + MIN_LEAD_SECONDS or start > now + MAX_AHEAD_DAYS * 86400:
                raise ApiError(422, "slot_time", f"A slot must start at least 5 minutes from now and within {MAX_AHEAD_DAYS} days.")
            if core.one("SELECT 1 FROM availability_slots WHERE doctor_id = ? AND status != 'cancelled' AND start_at < ? AND end_at > ?",
                        user["id"], end, start):
                raise ApiError(409, "slot_overlap", "This time overlaps a slot you already have.")
            slot_id = core.new_id()
            core.run("INSERT INTO availability_slots (id, doctor_id, start_at, end_at, created_at) VALUES (?,?,?,?,?)",
                     slot_id, user["id"], start, end, now)
            created.append(slot_id)
    found = [core.one("SELECT * FROM availability_slots WHERE id = ?", i) for i in created]
    return {"slots": [slot_view(s) for s in found]}


@route("/doctor/slots/{slot_id}", ["DELETE"], roles=("doctor",))
async def remove_slot(request, user, data):
    changed = core.run("UPDATE availability_slots SET status = 'cancelled' WHERE id = ? AND doctor_id = ? AND status = 'open'",
                       request.path_params["slot_id"], user["id"])
    if not changed:
        raise ApiError(409, "slot_not_open", "Only an open slot can be removed. Cancel the booking instead.")
    return {"removed": True}


# --------------------------------------------------------------------------- booking


def consulted_before(patient_id: str, doctor_id: str) -> bool:
    return bool(core.one("SELECT 1 FROM consultations WHERE patient_id = ? AND doctor_id = ? AND status = 'ended' LIMIT 1",
                         patient_id, doctor_id))


def bookable_doctor(patient: dict, doctor_id: str):
    d = core.one("SELECT d.* FROM doctor_profiles d JOIN users u ON u.id = d.user_id "
                 "WHERE d.user_id = ? AND d.status = 'approved' AND u.status = 'active'", doctor_id)
    if not d or not consulted_before(patient["id"], doctor_id):
        # Same answer for "no such doctor" and "not your doctor": the app is not a doctor directory.
        raise ApiError(403, "not_your_doctor", "You can book a doctor after you have had a call with them.")
    return d


@route("/doctors/{doctor_id}/slots", ["GET"], roles=("patient",))
async def doctor_slots(request, user, data):
    d = bookable_doctor(user, request.path_params["doctor_id"])
    found = core.rows("SELECT * FROM availability_slots WHERE doctor_id = ? AND status = 'open' AND start_at >= ? ORDER BY start_at LIMIT 100",
                      d["user_id"], core.now() + MIN_LEAD_SECONDS)
    return {"doctor": doctor_public(d), "price_taka": settings_store.get("booking_price_taka")[d["designation"]],
            "minutes": settings_store.get("booking_minutes"),
            "free_cancel_minutes": settings_store.get("booking_cancel_free_minutes"), "slots": [slot_view(s) for s in found]}


class NewBooking(BaseModel):
    slot_id: str
    problem: str = Field(min_length=2, max_length=300)


@route("/bookings", ["POST"], roles=("patient",), body=NewBooking)
async def book(request, user, data: NewBooking):
    if user["consent_version"] != settings_store.get("terms_version"):
        raise ApiError(409, "consent_needed", "Please accept the terms first.")
    now = core.now()
    with core.tx():
        slot = core.one("SELECT * FROM availability_slots WHERE id = ?", data.slot_id)
        if not slot or slot["status"] != "open" or slot["start_at"] < now + MIN_LEAD_SECONDS:
            raise ApiError(409, "slot_taken", "This time is no longer available. Please pick another.")
        d = bookable_doctor(user, slot["doctor_id"])
        if core.one("SELECT 1 FROM bookings WHERE patient_id = ? AND status = 'confirmed' AND start_at < ? AND end_at > ?",
                    user["id"], slot["end_at"], slot["start_at"]):
            raise ApiError(409, "booking_overlap", "You already have a booking at this time.")
        price = core.to_poisha(settings_store.get("booking_price_taka")[d["designation"]])
        pay = price * settings_store.get("booking_doctor_share_percent") // 100
        booking_id = core.new_id()
        wallet.add(user["id"], "charge", -price, "booking", booking_id, "Booking")
        core.run("UPDATE availability_slots SET status = 'booked' WHERE id = ?", slot["id"])
        core.run(
            "INSERT INTO bookings (id, slot_id, patient_id, doctor_id, start_at, end_at, problem, price_poisha, doctor_pay_poisha, "
            "designation, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            booking_id, slot["id"], user["id"], slot["doctor_id"], slot["start_at"], slot["end_at"], data.problem.strip(),
            price, int(pay), d["designation"], "confirmed", now)
    b = core.one("SELECT * FROM bookings WHERE id = ?", booking_id)
    await hub.send_all(notify(b))
    return booking_view(b, "patient")


@route("/bookings", ["GET"], roles=("patient", "doctor"))
async def my_bookings(request, user, data):
    """Upcoming bookings first (soonest first), then the 30 most recent past ones."""
    column = "patient_id" if user["role"] == "patient" else "doctor_id"
    upcoming = core.rows(f"SELECT * FROM bookings WHERE {column} = ? AND status IN ('confirmed', 'in_call') ORDER BY start_at", user["id"])
    past = core.rows(f"SELECT * FROM bookings WHERE {column} = ? AND status NOT IN ('confirmed', 'in_call') ORDER BY start_at DESC LIMIT 30",
                     user["id"])
    return {"upcoming": [booking_view(b, user["role"]) for b in upcoming], "past": [booking_view(b, user["role"]) for b in past]}


@route("/bookings/{booking_id}", ["GET"], roles=("patient", "doctor"))
async def get_booking(request, user, data):
    await matching.tick()
    return booking_view(load_for(request.path_params["booking_id"], user), user["role"])


def close(b, status: str, cancelled_by: str | None, refund: bool, pay_doctor: bool, reopen_slot: bool = False) -> Events:
    """End a booking that will not happen (or did not): move the money and free or close the slot."""
    with core.tx():
        refund_poisha = b["price_poisha"] if refund else 0
        paid = b["doctor_pay_poisha"] if pay_doctor else 0
        if refund_poisha:
            wallet.add(b["patient_id"], "refund", refund_poisha, "booking", b["id"], "Booking refunded")
        if paid:
            wallet.add(b["doctor_id"], "earning", paid, "booking", b["id"], "Booking: patient cancelled late or did not join")
        core.run("UPDATE bookings SET status = ?, cancelled_by = ?, refund_poisha = ?, doctor_paid_poisha = ?, closed_at = ? WHERE id = ?",
                 status, cancelled_by, refund_poisha, paid, core.now(), b["id"])
        core.run("UPDATE availability_slots SET status = ? WHERE id = ?", "open" if reopen_slot else "cancelled", b["slot_id"])
    return notify(b)


def cancel(b, by_role: str) -> Events:
    now = core.now()
    if b["status"] != "confirmed" or now >= b["start_at"]:
        raise ApiError(409, "cannot_cancel", "This booking can no longer be cancelled.")
    if by_role == "patient":
        early = b["start_at"] - now >= settings_store.get("booking_cancel_free_minutes") * 60
        return close(b, "cancelled", "patient", refund=early, pay_doctor=not early, reopen_slot=early)
    return close(b, "cancelled", by_role, refund=True, pay_doctor=False)


@route("/bookings/{booking_id}/cancel", ["POST"], roles=("patient", "doctor"))
async def cancel_booking(request, user, data):
    b = load_for(request.path_params["booking_id"], user)
    await hub.send_all(cancel(b, user["role"]))
    return booking_view(core.one("SELECT * FROM bookings WHERE id = ?", b["id"]), user["role"])


def cancel_all_for_doctor(doctor_id: str) -> Events:
    """Used when a doctor is suspended: every booking that has not started is refunded in full."""
    events: Events = []
    for b in core.rows("SELECT * FROM bookings WHERE doctor_id = ? AND status = 'confirmed'", doctor_id):
        events += close(b, "cancelled", "system", refund=True, pay_doctor=False)
    core.run("UPDATE availability_slots SET status = 'cancelled' WHERE doctor_id = ? AND status = 'open'", doctor_id)
    return events


def join(user: dict, b) -> Events:
    """One side says "I am here". When both have, the booked call starts and runs until the slot's end time."""
    now = core.now()
    if b["status"] == "in_call":
        return []
    if b["status"] != "confirmed":
        raise ApiError(409, "booking_closed", "This booking is no longer open.")
    if now < b["start_at"] - JOIN_EARLY_SECONDS:
        raise ApiError(409, "too_early", "You can join 2 minutes before the start time.")
    if now >= b["end_at"]:
        raise ApiError(409, "booking_closed", "This booking's time has passed.")
    role = user["role"]
    column, live = ("patient_id", LIVE_FOR_PATIENT) if role == "patient" else ("doctor_id", LIVE_FOR_DOCTOR)
    if core.one(f"SELECT 1 FROM consultations WHERE {column} = ? AND status IN {live}", user["id"]):
        raise ApiError(409, "already_in_call", "Finish or cancel your current call first.")
    with core.tx():
        core.run(f"UPDATE bookings SET {role}_joined_at = COALESCE({role}_joined_at, ?) WHERE id = ?", now, b["id"])
        b = core.one("SELECT * FROM bookings WHERE id = ?", b["id"])
        if b["patient_joined_at"] is None or b["doctor_joined_at"] is None:
            return notify(b)
        consultation_id = core.new_id()
        core.run(
            "INSERT INTO consultations (id, type, patient_id, doctor_id, status, problem, hold_poisha, held, max_seconds, "
            "free_skip_seconds, pay_per_call_poisha, pay_per_minute_poisha, designation, created_at, started_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            consultation_id, "booked", b["patient_id"], b["doctor_id"], "active", b["problem"], 0, 0,
            max(60, int(b["end_at"] - now)), 0, b["doctor_pay_poisha"], 0, b["designation"], now, now)
        core.run("UPDATE bookings SET status = 'in_call', consultation_id = ? WHERE id = ?", consultation_id, b["id"])
    return notify(b) + matching.both(consultation_id)


@route("/bookings/{booking_id}/join", ["POST"], roles=("patient", "doctor"))
async def join_booking(request, user, data):
    await matching.tick()
    b = load_for(request.path_params["booking_id"], user)
    await hub.send_all(join(user, b))
    return booking_view(core.one("SELECT * FROM bookings WHERE id = ?", b["id"]), user["role"])


# --------------------------------------------------------------------------- housekeeping


def sweep() -> Events:
    now = core.now()
    events: Events = []
    lead = settings_store.get("booking_reminder_minutes") * 60
    if lead:
        for b in core.rows("SELECT * FROM bookings WHERE status = 'confirmed' AND reminder_sent = 0 AND start_at - ? <= ? AND start_at > ?",
                           lead, now, now):
            core.run("UPDATE bookings SET reminder_sent = 1 WHERE id = ?", b["id"])
            for user_id in (b["patient_id"], b["doctor_id"]):
                try:
                    send_sms(core.one("SELECT phone FROM users WHERE id = ?", user_id)["phone"],
                             "10 Takar Doctor: your booked call starts soon. Open the app to join.")
                except ApiError:
                    pass   # no SMS provider yet: the app still shows the booking
    for b in core.rows("SELECT * FROM bookings WHERE status = 'confirmed' AND start_at + ? <= ?",
                       settings_store.get("booking_no_show_minutes") * 60, now):
        patient_here, doctor_here = b["patient_joined_at"] is not None, b["doctor_joined_at"] is not None
        if doctor_here and not patient_here:
            events += close(b, "patient_no_show", None, refund=False, pay_doctor=True)
        elif patient_here and not doctor_here:
            events += close(b, "doctor_no_show", None, refund=True, pay_doctor=False)
        else:
            events += close(b, "both_no_show", None, refund=True, pay_doctor=False)
    return events


def next_booking(user: dict) -> dict:
    """Added to GET /consultations/current so one poll tells the app about a booked call that is coming up."""
    column = "patient_id" if user["role"] == "patient" else "doctor_id"
    b = core.one(f"SELECT * FROM bookings WHERE {column} = ? AND status = 'confirmed' ORDER BY start_at LIMIT 1", user["id"])
    return {"next_booking": booking_view(b, user["role"]) if b else None}


matching.EXTRA_SWEEPS.append(sweep)
matching.CURRENT_EXTRAS.append(next_booking)
