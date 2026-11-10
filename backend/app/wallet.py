"""Prepaid wallet. Every change to a balance is one row in wallet_transactions."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import core, settings_store
from .api import route
from .core import ApiError


def add(user_id: str, kind: str, amount_poisha: int, ref_type: str | None = None, ref_id: str | None = None,
        note: str | None = None) -> int:
    """Apply a signed amount to a balance and record it. Refuses to take a balance below zero."""
    with core.tx():
        row = core.one("SELECT balance_poisha FROM users WHERE id = ?", user_id)
        balance = row["balance_poisha"] + amount_poisha
        if balance < 0:
            raise ApiError(402, "insufficient_balance", "Your balance is too low. Please top up.")
        core.run("UPDATE users SET balance_poisha = ? WHERE id = ?", balance, user_id)
        core.run(
            "INSERT INTO wallet_transactions (id, user_id, type, amount_poisha, balance_after_poisha, ref_type, ref_id, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            core.new_id(), user_id, kind, amount_poisha, balance, ref_type, ref_id, note, core.now(),
        )
    return balance


def mark_topup_paid(topup_id: str, gateway_ref: str) -> dict:
    """Credit a top-up exactly once. Call this from the payment gateway's confirmation callback."""
    with core.tx():
        topup = core.one("SELECT * FROM topups WHERE id = ?", topup_id)
        if not topup:
            raise ApiError(404, "not_found", "Top-up not found.")
        if topup["status"] == "pending":
            core.run("UPDATE topups SET status = 'paid', gateway_ref = ?, paid_at = ? WHERE id = ?",
                     gateway_ref, core.now(), topup_id)
            add(topup["user_id"], "topup", topup["amount_poisha"], "topup", topup_id, f"Top-up by {topup['method']}")
    return dict(core.one("SELECT * FROM topups WHERE id = ?", topup_id))


@route("/wallet", ["GET"], roles=("patient", "doctor"))
async def get_wallet(request, user, data):
    txns = core.rows(
        "SELECT type, amount_poisha, balance_after_poisha, note, created_at FROM wallet_transactions "
        "WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 50", user["id"])
    return {
        "balance_taka": core.to_taka(user["balance_poisha"]),
        "transactions": [
            {"type": t["type"], "amount_taka": core.to_taka(t["amount_poisha"]),
             "balance_after_taka": core.to_taka(t["balance_after_poisha"]), "note": t["note"], "created_at": t["created_at"]}
            for t in txns
        ],
    }


class TopupRequest(BaseModel):
    amount_taka: float = Field(gt=0, le=50000)
    method: Literal["bkash", "nagad", "card"]


@route("/wallet/topups", ["POST"], roles=("patient", "doctor"), body=TopupRequest)
async def create_topup(request, user, data: TopupRequest):
    minimum = settings_store.get("min_topup_taka")
    if data.amount_taka < minimum:
        raise ApiError(422, "topup_too_small", f"The smallest top-up is {minimum} taka.")
    topup_id = core.new_id()
    core.run("INSERT INTO topups (id, user_id, amount_poisha, method, created_at) VALUES (?,?,?,?,?)",
             topup_id, user["id"], core.to_poisha(data.amount_taka), data.method, core.now())
    # The real gateway integration goes here: create the payment with bKash/Nagad and return its checkout link.
    return {"id": topup_id, "status": "pending", "amount_taka": data.amount_taka, "payment_url": None}


@route("/dev/topups/{topup_id}/confirm", ["POST"], roles=("patient", "doctor"))
async def dev_confirm_topup(request, user, data):
    """Development only: pretend the gateway confirmed the payment."""
    if not core.config.dev_mode:
        raise ApiError(404, "not_found", "Not found.")
    topup = core.one("SELECT user_id FROM topups WHERE id = ?", request.path_params["topup_id"])
    if not topup or topup["user_id"] != user["id"]:
        raise ApiError(404, "not_found", "Top-up not found.")
    paid = mark_topup_paid(request.path_params["topup_id"], "dev")
    return {"id": paid["id"], "status": paid["status"]}
