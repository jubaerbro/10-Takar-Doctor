from app import accounts


def balance(api, token):
    return api.ok(api.get("/wallet", token))["balance_taka"]


def current(api, token):
    return api.ok(api.get("/consultations/current", token))


def had_a_call(api, designation="consultant", patient_balance=500):
    """A patient and a doctor who have completed one instant call, so the patient may book the doctor."""
    patient, patient_id = api.patient(balance=patient_balance)
    doctor, doctor_id = api.doctor(designation=designation)
    c = api.ok(api.post("/match", patient, {"problem": "fever"}))
    api.ok(api.post(f"/consultations/{c['id']}/accept", doctor))
    api.wait(60, doctor)
    api.ok(api.post(f"/consultations/{c['id']}/end", patient))
    api.ok(api.post("/doctor/offline", doctor))
    return patient, doctor, doctor_id


def open_slot(api, doctor, in_seconds=3600):
    start = api.clock.t + in_seconds
    return api.ok(api.post("/doctor/slots", doctor, {"starts": [start]}))["slots"][0]


def booked(api, in_seconds=3600, **kw):
    patient, doctor, doctor_id = had_a_call(api, **kw)
    slot = open_slot(api, doctor, in_seconds)
    booking = api.ok(api.post("/bookings", patient, {"slot_id": slot["id"], "problem": "follow-up on fever"}))
    return patient, doctor, doctor_id, booking


def test_doctor_manages_bookable_hours(api):
    doctor, _ = api.doctor(online=False)
    now = api.clock.t
    made = api.ok(api.post("/doctor/slots", doctor, {"starts": [now + 3600, now + 3600 + 900]}))["slots"]
    assert [s["end_at"] - s["start_at"] for s in made] == [900, 900]            # 15-minute slots, back to back
    assert api.post("/doctor/slots", doctor, {"starts": [now + 3600 + 300]})[1]["error"]["code"] == "slot_overlap"
    assert api.post("/doctor/slots", doctor, {"starts": [now + 60]})[1]["error"]["code"] == "slot_time"
    assert api.post("/doctor/slots", doctor, {"starts": [now + 40 * 86400]})[1]["error"]["code"] == "slot_time"
    assert api.call("DELETE", f"/doctor/slots/{made[1]['id']}", doctor)[0] == 200
    assert [s["id"] for s in api.ok(api.get("/doctor/slots", doctor))["slots"]] == [made[0]["id"]]
    pending, _ = api.doctor_pending()
    assert api.post("/doctor/slots", pending, {"starts": [now + 3600]})[1]["error"]["code"] == "not_verified"


def test_only_a_doctor_you_have_consulted_can_be_booked(api):
    patient, doctor, doctor_id = had_a_call(api)
    slot = open_slot(api, doctor)
    stranger, _ = api.patient(balance=500)
    assert api.get(f"/doctors/{doctor_id}/slots", stranger)[1]["error"]["code"] == "not_your_doctor"
    assert api.post("/bookings", stranger, {"slot_id": slot["id"], "problem": "hello"})[1]["error"]["code"] == "not_your_doctor"
    assert api.get("/doctors/no-such-doctor/slots", patient)[1]["error"]["code"] == "not_your_doctor"

    offer = api.ok(api.get(f"/doctors/{doctor_id}/slots", patient))
    assert offer["price_taka"] == 200 and offer["minutes"] == 15 and [s["id"] for s in offer["slots"]] == [slot["id"]]
    assert offer["doctor"]["name"] == "Dr Nasrin Akter"


def test_booking_charges_now_and_the_call_pays_the_doctor(api):
    patient, doctor, doctor_id, booking = booked(api, in_seconds=3600)
    assert booking["status"] == "confirmed" and booking["price_taka"] == 200
    assert balance(api, patient) == 500 - 10 - 200                              # one instant call, then the booking
    assert api.ok(api.get(f"/doctors/{doctor_id}/slots", patient))["slots"] == []   # the slot is taken
    assert current(api, patient)["next_booking"]["id"] == booking["id"]
    seen_by_doctor = current(api, doctor)["next_booking"]
    assert seen_by_doctor["pay_taka"] == 140 and seen_by_doctor["patient"]["age"] == 30 and "doctor" not in seen_by_doctor

    assert api.post(f"/bookings/{booking['id']}/join", patient)[1]["error"]["code"] == "too_early"
    api.clock.forward(3600 - 100)                                                # 100 seconds before the start
    assert api.ok(api.post(f"/bookings/{booking['id']}/join", patient)) | {} and current(api, patient)["consultation"] is None
    waiting = api.ok(api.get(f"/bookings/{booking['id']}", doctor))
    assert waiting["other_joined"] and not waiting["i_joined"]
    started = api.ok(api.post(f"/bookings/{booking['id']}/join", doctor))
    assert started["status"] == "in_call"

    call = current(api, patient)["consultation"]
    assert call["type"] == "booked" and call["status"] == "active" and call["id"] == started["consultation_id"]
    assert call["ends_at"] == booking["end_at"] and call["doctor"]["bmdc_number"] == "A-12345"
    assert current(api, doctor)["consultation"]["problem"] == "follow-up on fever"
    assert api.post("/match", patient, {"problem": "other"})[1]["error"]["code"] == "already_in_call"

    before = balance(api, doctor)
    api.clock.forward(2000)                                                      # nobody hangs up: the slot's end closes the call
    assert current(api, patient)["consultation"] is None
    ended = api.ok(api.get(f"/consultations/{call['id']}", patient))
    assert ended["status"] == "ended" and ended["ended_by"] == "system" and ended["price_charged_taka"] == 200
    assert balance(api, doctor) == before + 140 and balance(api, patient) == 290    # 70 percent to the doctor, no second charge
    done = api.ok(api.get("/bookings", patient))
    assert done["upcoming"] == [] and done["past"][0]["status"] == "completed"
    api.ok(api.post(f"/consultations/{call['id']}/rating", patient, {"rating": 5}))
    api.ok(api.put(f"/consultations/{call['id']}/advice-note", doctor, {"assessment": "Recovering well"}))


def test_cancellation_rules(api):
    patient, doctor, doctor_id, booking = booked(api, in_seconds=4 * 3600)
    assert balance(api, patient) == 290
    assert api.ok(api.post(f"/bookings/{booking['id']}/cancel", patient))["refund_taka"] == 200   # early: full refund
    assert balance(api, patient) == 490
    slot = api.ok(api.get(f"/doctors/{doctor_id}/slots", patient))["slots"][0]                   # and the slot is open again

    late = api.ok(api.post("/bookings", patient, {"slot_id": slot["id"], "problem": "again"}))
    api.clock.forward(4 * 3600 - 30 * 60)                                        # 30 minutes before: too late for a refund
    before = balance(api, doctor)
    cancelled = api.ok(api.post(f"/bookings/{late['id']}/cancel", patient))
    assert cancelled["status"] == "cancelled" and cancelled["refund_taka"] == 0
    assert balance(api, patient) == 290 and balance(api, doctor) == before + 140
    assert api.post(f"/bookings/{late['id']}/cancel", patient)[1]["error"]["code"] == "cannot_cancel"

    slot = open_slot(api, doctor, 3600)
    third = api.ok(api.post("/bookings", patient, {"slot_id": slot["id"], "problem": "third"}))
    api.clock.forward(3600 - 60)
    api.ok(api.post(f"/bookings/{third['id']}/cancel", doctor))                 # the doctor cancels a minute before
    assert balance(api, patient) == 290 and api.ok(api.get(f"/bookings/{third['id']}", patient))["cancelled_by"] == "doctor"


def test_no_shows(api):
    patient, doctor, _, booking = booked(api, in_seconds=1000)
    api.clock.forward(1000 - 60)
    api.ok(api.post(f"/bookings/{booking['id']}/join", patient))
    api.clock.forward(60 + 5 * 60 + 1)                                           # the doctor never comes
    assert api.ok(api.get(f"/bookings/{booking['id']}", patient))["status"] == "doctor_no_show"
    assert balance(api, patient) == 490                                          # refunded in full

    slot = open_slot(api, doctor, 1000)
    second = api.ok(api.post("/bookings", patient, {"slot_id": slot["id"], "problem": "second"}))
    api.clock.forward(1000)
    before = balance(api, doctor)
    api.ok(api.post(f"/bookings/{second['id']}/join", doctor))
    api.clock.forward(5 * 60 + 1)                                                # the patient never comes
    assert api.ok(api.get(f"/bookings/{second['id']}", doctor))["status"] == "patient_no_show"
    assert balance(api, patient) == 290 and balance(api, doctor) == before + 140
    assert api.post(f"/bookings/{second['id']}/join", patient)[1]["error"]["code"] == "booking_closed"

    slot = open_slot(api, doctor, 1000)
    third = api.ok(api.post("/bookings", patient, {"slot_id": slot["id"], "problem": "third"}))
    api.clock.forward(1000 + 5 * 60 + 1)                                         # neither comes
    assert api.ok(api.get(f"/bookings/{third['id']}", patient))["status"] == "both_no_show"
    assert balance(api, patient) == 290


def test_booking_needs_money_and_a_free_slot(api):
    patient, doctor, doctor_id = had_a_call(api, patient_balance=100)
    slot = open_slot(api, doctor)
    assert api.post("/bookings", patient, {"slot_id": slot["id"], "problem": "x1"})[1]["error"]["code"] == "insufficient_balance"
    assert api.ok(api.get(f"/doctors/{doctor_id}/slots", patient))["slots"][0]["id"] == slot["id"]   # still open

    rich, other_doctor, _, booking = booked(api)
    other, _ = api.patient(balance=500)
    assert api.post("/bookings", other, {"slot_id": booking["id"], "problem": "x2"})[1]["error"]["code"] == "slot_taken"


def test_doctor_with_a_booking_about_to_start_gets_no_instant_calls(api):
    patient, doctor, _, booking = booked(api, in_seconds=3600)
    api.ok(api.post("/doctor/online", doctor))
    walk_in, _ = api.patient()
    c = api.ok(api.post("/match", walk_in, {"problem": "cough"}))
    assert c["status"] == "offered"                                              # an hour away: instant calls are fine
    api.ok(api.post(f"/consultations/{c['id']}/cancel", walk_in))
    api.wait(3600 - 5 * 60, doctor)                                              # five minutes before the booking
    assert api.ok(api.post("/match", walk_in, {"problem": "cough"}))["status"] == "waiting"


def test_reminder_is_sent_once(api, monkeypatch):
    sent = []
    monkeypatch.setattr("app.bookings.send_sms", lambda phone, text: sent.append(phone))
    patient, doctor, _, booking = booked(api, in_seconds=3600)
    api.clock.forward(3600 - 11 * 60)
    current(api, patient)
    assert sent == []
    api.clock.forward(2 * 60)                                                    # nine minutes before
    current(api, patient); current(api, patient)
    assert len(sent) == 2 and len(set(sent)) == 2                                # one each, not repeated


def test_suspending_a_doctor_refunds_their_bookings_and_admin_can_list_them(api):
    patient, doctor, doctor_id, booking = booked(api)
    admin = api.admin()
    listed = api.ok(api.get("/admin/bookings", admin))["bookings"]
    assert len(listed) == 1 and listed[0]["doctor"]["name"] == "Dr Nasrin Akter" and listed[0]["price_taka"] == 200
    assert api.ok(api.get("/admin/dashboard", admin))["bookings_upcoming"] == 1
    api.ok(api.post(f"/admin/users/{doctor_id}/suspend", admin, {"suspended": True, "reason": "test"}))
    assert balance(api, patient) == 490
    past = api.ok(api.get("/admin/bookings?when=past", admin))["bookings"]
    assert past[0]["status"] == "cancelled" and past[0]["cancelled_by"] == "system" and past[0]["refund_taka"] == 200
