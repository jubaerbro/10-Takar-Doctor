from app import core


def balance(api, token):
    return api.ok(api.get("/wallet", token))["balance_taka"]


def current(api, token):
    return api.ok(api.get("/consultations/current", token))["consultation"]


def connect(api, patient, doctor, **match):
    """Patient asks for a doctor, the doctor accepts. Returns the consultation id."""
    c = api.ok(api.post("/match", patient, {"problem": "fever for two days", **match}))
    assert c["status"] == "offered" and c["doctor"] is None      # doctor stays hidden until they accept
    offer = current(api, doctor)
    assert offer["id"] == c["id"] and offer["status"] == "offered"
    assert offer["patient"] == {"alias": offer["patient"]["alias"], "age": 30, "gender": "male"}   # no name, no phone
    api.ok(api.post(f"/consultations/{c['id']}/accept", doctor))
    return c["id"]


def test_full_call_charges_patient_and_pays_doctor(api):
    patient, _ = api.patient(balance=100)
    doctor, doctor_id = api.doctor(designation="consultant")
    cid = connect(api, patient, doctor)
    assert balance(api, patient) == 90                           # 10 taka held while the call runs

    seen = current(api, patient)
    assert seen["status"] == "active" and seen["max_seconds"] == 600
    assert seen["doctor"]["name"] == "Dr Nasrin Akter" and seen["doctor"]["bmdc_number"] == "A-12345" and seen["doctor"]["verified"]

    api.wait(4 * 60 + 20, doctor)                                # 4 min 20 s -> billed as 5 minutes of doctor time
    ended = api.ok(api.post(f"/consultations/{cid}/end", patient))
    assert ended["status"] == "ended" and ended["price_charged_taka"] == 10 and ended["duration_seconds"] == 260
    assert balance(api, patient) == 90
    assert balance(api, doctor) == 8 + 5 * 0.8                   # consultant: 8 per call + 0.8 per minute
    assert api.ok(api.get("/doctor/earnings", doctor))["today"] == {"calls": 1, "earned_taka": 12.0}

    # after the call: advice note, rating, history
    note = {"assessment": "Likely viral fever", "self_care": "Rest and fluids", "referral": "See a doctor in person if it lasts 3 more days"}
    assert api.put(f"/consultations/{cid}/advice-note", patient, note)[0] == 403
    api.ok(api.put(f"/consultations/{cid}/advice-note", doctor, note))
    api.ok(api.post(f"/consultations/{cid}/rating", patient, {"rating": 4}))
    assert api.post(f"/consultations/{cid}/rating", patient, {"rating": 5})[1]["error"]["code"] == "already_rated"
    past = api.ok(api.get("/consultations", patient))["consultations"][0]
    assert past["advice_note"]["assessment"] == "Likely viral fever" and past["rating"] == 4
    assert past["doctor"]["rating"] == 4.0 and past["doctor"]["rating_count"] == 1
    assert current(api, patient) is None and current(api, doctor) is None


def test_quick_skip_within_three_seconds_is_free(api):
    patient, _ = api.patient(balance=10)
    doctor, _ = api.doctor()
    cid = connect(api, patient, doctor)
    api.clock.forward(2)
    ended = api.ok(api.post(f"/consultations/{cid}/end", patient))
    assert ended["status"] == "skipped" and ended["price_charged_taka"] == 0
    assert balance(api, patient) == 10 and balance(api, doctor) == 0

    cid = connect(api, patient, doctor)                          # same doctor is free again
    api.clock.forward(4)                                         # past the free window: charged
    assert api.ok(api.post(f"/consultations/{cid}/end", patient))["status"] == "ended"
    assert balance(api, patient) == 0
    status, body = api.post("/match", patient, {"problem": "cough"})
    assert status == 402 and body["error"]["code"] == "insufficient_balance"
    assert current(api, patient) is None                         # a refused match leaves nothing behind


def test_free_skips_are_limited_per_day(api):
    api.ok(api.put("/admin/settings", api.admin(), {"free_skips_per_day": 2}))
    patient, _ = api.patient(balance=50)
    doctor, _ = api.doctor()
    for expected in ("skipped", "skipped", "ended"):
        cid = connect(api, patient, doctor)
        api.clock.forward(1)
        assert api.ok(api.post(f"/consultations/{cid}/end", patient))["status"] == expected
    assert balance(api, patient) == 40
    api.wait(86400, doctor)                                      # next day: free skips are back
    cid = connect(api, patient, doctor)
    assert api.ok(api.post(f"/consultations/{cid}/end", patient))["status"] == "skipped"


def test_doctor_hanging_up_at_once_costs_the_patient_nothing(api):
    patient, _ = api.patient(balance=10)
    doctor, _ = api.doctor()
    cid = connect(api, patient, doctor)
    api.clock.forward(1)
    assert api.ok(api.post(f"/consultations/{cid}/end", doctor))["status"] == "skipped"
    assert balance(api, patient) == 10 and balance(api, doctor) == 0


def test_call_is_cut_off_at_ten_minutes(api):
    patient, _ = api.patient()
    doctor, _ = api.doctor(designation="professor")
    cid = connect(api, patient, doctor)
    api.wait(599, doctor)
    assert current(api, patient)["status"] == "active"
    api.clock.forward(30)
    assert current(api, patient) is None                         # the sweep ended it
    ended = api.ok(api.get(f"/consultations/{cid}", patient))
    assert ended["status"] == "ended" and ended["ended_by"] == "system" and ended["duration_seconds"] == 600
    assert balance(api, doctor) == 20 + 10 * 2                   # professor: 20 per call + 2 per minute, capped at 10 minutes


def test_price_changes_apply_to_new_calls_only(api):
    admin = api.admin()
    patient, _ = api.patient(balance=100)
    doctor, _ = api.doctor()
    cid = connect(api, patient, doctor)
    api.ok(api.put("/admin/settings", admin, {"instant_call_price_taka": 15, "instant_call_max_minutes": 5}))
    api.wait(360, doctor)                                        # 6 minutes: still allowed under the old 10-minute cap
    ended = api.ok(api.post(f"/consultations/{cid}/end", patient))
    assert ended["price_charged_taka"] == 10 and ended["duration_seconds"] == 360
    cid = connect(api, patient, doctor)
    assert current(api, patient)["max_seconds"] == 300 and balance(api, patient) == 75
    api.clock.forward(60)
    assert api.ok(api.post(f"/consultations/{cid}/end", patient))["price_charged_taka"] == 15


def test_decline_and_timeout_move_to_next_doctor(api):
    patient, _ = api.patient()
    first, first_id = api.doctor()
    api.clock.forward(1)
    second, _ = api.doctor()
    api.clock.forward(1)
    third, _ = api.doctor()
    c = api.ok(api.post("/match", patient, {"problem": "rash"}))
    assert current(api, first)["id"] == c["id"]                  # longest-waiting doctor is asked first
    api.ok(api.post(f"/consultations/{c['id']}/decline", first))
    assert current(api, first) is None and current(api, second)["id"] == c["id"]

    api.clock.forward(16)                                        # second doctor lets it ring out
    api.ok(api.post("/doctor/heartbeat", third))
    assert current(api, third)["id"] == c["id"]
    assert api.ok(api.post("/doctor/heartbeat", second)) == {"online": False}   # and is set offline
    assert api.post(f"/consultations/{c['id']}/accept", second)[1]["error"]["code"] == "offer_gone"
    api.ok(api.post(f"/consultations/{c['id']}/accept", third))
    assert current(api, patient)["status"] == "active"


def test_waiting_request_reaches_doctor_who_comes_online(api):
    patient, _ = api.patient(balance=10)
    general, _ = api.doctor(specialty="general")
    c = api.ok(api.post("/match", patient, {"problem": "itchy rash", "specialty": "skin"}))
    assert c["status"] == "waiting" and c["from_waiting_request"] and c["expires_at"] == c["waiting_since"] + 360 * 60
    assert balance(api, patient) == 10                           # nothing is held while the request waits
    assert current(api, general) is None                         # wrong type of doctor: not asked
    assert api.post("/match", patient, {"problem": "again"})[1]["error"]["code"] == "already_in_call"

    api.clock.forward(40 * 60)                                   # 40 minutes later a skin doctor comes online
    skin, _ = api.doctor(specialty="skin")
    offer = current(api, skin)
    assert offer["id"] == c["id"] and offer["status"] == "offered" and offer["from_waiting_request"]
    assert offer["problem"] == "itchy rash" and offer["patient"]["age"] == 30

    ready = api.ok(api.post(f"/consultations/{c['id']}/accept", skin))
    assert ready["status"] == "ready" and ready["join_by"] == api.clock.t + 60
    assert balance(api, patient) == 0                            # held only now that a doctor is ready
    seen = current(api, patient)
    assert seen["status"] == "ready" and seen["doctor"]["specialty"] == "skin"
    assert api.post(f"/consultations/{c['id']}/join", skin)[0] == 403

    api.clock.forward(20)
    started = api.ok(api.post(f"/consultations/{c['id']}/join", patient))
    assert started["status"] == "active" and started["started_at"] == api.clock.t   # the 10 minutes start at joining
    api.wait(120, skin)
    assert api.ok(api.post(f"/consultations/{c['id']}/end", patient))["price_charged_taka"] == 10
    assert balance(api, patient) == 0 and balance(api, skin) == 5 + 2 * 0.5


def test_patient_who_does_not_join_loses_the_request_after_two_misses(api):
    patient, _ = api.patient(balance=10)
    c = api.ok(api.post("/match", patient, {"problem": "headache"}))
    first, _ = api.doctor()
    api.ok(api.post(f"/consultations/{c['id']}/accept", first))
    api.clock.forward(61)                                        # patient never joins
    api.ok(api.post("/doctor/heartbeat", first))
    assert current(api, first) is None                           # doctor is freed, and not asked again for this request
    assert current(api, patient)["status"] == "waiting" and balance(api, patient) == 10
    assert api.post(f"/consultations/{c['id']}/join", patient)[1]["error"]["code"] == "not_ready"

    second, _ = api.doctor()
    api.ok(api.post(f"/consultations/{c['id']}/accept", second))
    api.clock.forward(61)
    assert current(api, patient) is None
    assert api.ok(api.get(f"/consultations/{c['id']}", patient))["status"] == "cancelled"
    assert balance(api, patient) == 10 and balance(api, first) == 0 and balance(api, second) == 0


def test_waiting_request_expires_or_can_be_cancelled(api):
    patient, _ = api.patient(balance=10)
    c = api.ok(api.post("/match", patient, {"problem": "headache"}))
    api.clock.forward(360 * 60 + 1)
    assert current(api, patient) is None
    assert api.ok(api.get(f"/consultations/{c['id']}", patient))["status"] == "no_doctor"

    c = api.ok(api.post("/match", patient, {"problem": "headache"}))
    assert api.ok(api.post(f"/consultations/{c['id']}/cancel", patient))["status"] == "cancelled"
    doctor, _ = api.doctor()
    assert current(api, doctor) is None                          # a cancelled request never reaches a doctor

    c = api.ok(api.post("/match", patient, {"problem": "headache"}))
    assert c["status"] == "offered" and balance(api, patient) == 0
    assert api.ok(api.post(f"/consultations/{c['id']}/cancel", patient))["status"] == "cancelled"
    assert balance(api, patient) == 10 and current(api, doctor) is None
    assert api.post(f"/consultations/{c['id']}/accept", doctor)[0] == 409


def test_waiting_request_is_dropped_if_the_patient_spent_the_money(api):
    patient, patient_id = api.patient(balance=10)
    c = api.ok(api.post("/match", patient, {"problem": "headache"}))
    api.ok(api.post("/admin/wallet/adjust", api.admin(), {"user_id": patient_id, "amount_taka": -10, "reason": "test"}))
    doctor, _ = api.doctor()
    status, body = api.post(f"/consultations/{c['id']}/accept", doctor)
    assert status == 409 and body["error"]["code"] == "offer_gone"
    assert current(api, doctor) is None and current(api, patient) is None
    assert api.ok(api.get(f"/consultations/{c['id']}", patient))["status"] == "cancelled"


def test_doctor_can_decline_or_leave_a_waiting_request(api):
    patient, _ = api.patient(balance=10)
    c = api.ok(api.post("/match", patient, {"problem": "headache"}))
    first, _ = api.doctor()
    api.ok(api.post(f"/consultations/{c['id']}/decline", first))
    assert current(api, patient)["status"] == "waiting" and current(api, first) is None

    second, _ = api.doctor()
    api.ok(api.post(f"/consultations/{c['id']}/accept", second))
    api.ok(api.post("/doctor/offline", second))                  # doctor leaves before the patient joins
    waiting = current(api, patient)
    assert waiting["status"] == "waiting" and balance(api, patient) == 10

    third, _ = api.doctor()
    api.ok(api.post(f"/consultations/{c['id']}/accept", third))
    api.clock.forward(61)                                        # first real miss: the doctor leaving did not count
    assert current(api, patient)["status"] == "waiting"


def test_oldest_waiting_request_is_served_first(api):
    a, _ = api.patient()
    b, _ = api.patient()
    first = api.ok(api.post("/match", a, {"problem": "fever"}))
    api.clock.forward(5)
    second = api.ok(api.post("/match", b, {"problem": "cough"}))
    doctor, _ = api.doctor()
    assert current(api, doctor)["id"] == first["id"]
    api.ok(api.post(f"/consultations/{first['id']}/accept", doctor))
    api.ok(api.post(f"/consultations/{first['id']}/join", a))
    api.wait(60, doctor)
    api.ok(api.post(f"/consultations/{first['id']}/end", a))
    assert current(api, doctor)["id"] == second["id"]            # then the next one in line


def test_filters(api):
    patient, _ = api.patient()
    general, _ = api.doctor(specialty="general", gender="male", languages=("bn",))
    skin, _ = api.doctor(specialty="skin", gender="female", languages=("bn", "en"))
    c = api.ok(api.post("/match", patient, {"problem": "rash", "specialty": "skin", "language": "en", "doctor_gender": "female"}))
    assert current(api, skin)["id"] == c["id"] and current(api, general) is None
    api.ok(api.post(f"/consultations/{c['id']}/cancel", patient))
    c = api.ok(api.post("/match", patient, {"problem": "rash", "specialty": "child"}))
    assert c["status"] == "waiting"                              # nobody matches: becomes a waiting request
    assert api.post("/match", api.patient()[0], {"problem": "x1", "specialty": "astrology"})[0] == 422


def test_doctor_gets_one_call_at_a_time_and_stale_doctors_drop_off(api):
    doctor, _ = api.doctor()
    a, _ = api.patient()
    b, _ = api.patient()
    cid = connect(api, a, doctor)
    waiting = api.ok(api.post("/match", b, {"problem": "cough"}))
    assert waiting["status"] == "waiting"                        # the only doctor is busy
    api.clock.forward(30)
    api.ok(api.post(f"/consultations/{cid}/end", a))
    assert current(api, doctor)["id"] == waiting["id"]           # freed doctor gets the next patient
    api.ok(api.post(f"/consultations/{waiting['id']}/cancel", b))

    api.clock.forward(100)                                       # no heartbeat for 100 s
    assert api.ok(api.post("/match", b, {"problem": "cough"}))["status"] == "waiting"


def test_requirements_before_matching(api):
    token, _ = api.login("patient")
    assert api.post("/match", token, {"problem": "fever"})[1]["error"]["code"] == "consent_needed"
    api.ok(api.post("/me/consent", token, {"version": "1"}))
    assert api.post("/match", token, {"problem": "fever"})[1]["error"]["code"] == "profile_missing"
    patient, _ = api.patient()
    api.ok(api.put("/admin/settings", api.admin(), {"terms_version": "2"}))
    assert api.post("/match", patient, {"problem": "fever"})[1]["error"]["code"] == "consent_needed"   # old terms no longer count


def test_reports_suspension_and_wallet_adjustment(api):
    admin = api.admin()
    patient, patient_id = api.patient(balance=20)
    doctor, doctor_id = api.doctor()
    cid = connect(api, patient, doctor)
    api.clock.forward(60)
    api.ok(api.post(f"/consultations/{cid}/end", patient))
    api.ok(api.post(f"/consultations/{cid}/report", patient, {"reason": "prescribed_medicine", "details": "Told me to take antibiotics"}))
    reports = api.ok(api.get("/admin/reports", admin))["reports"]
    assert len(reports) == 1 and reports[0]["target_id"] == doctor_id
    api.ok(api.post(f"/admin/users/{doctor_id}/suspend", admin, {"suspended": True, "reason": "Prescribed medicine"}))
    api.ok(api.post(f"/admin/reports/{reports[0]['id']}/resolve", admin, {"resolution": "Doctor suspended"}))
    assert api.get("/me", doctor)[1]["error"]["code"] == "suspended"
    request = api.ok(api.post("/match", patient, {"problem": "fever"}))
    assert request["status"] == "waiting"                        # suspended doctor gets no calls

    api.ok(api.post("/admin/wallet/adjust", admin, {"user_id": patient_id, "amount_taka": 10, "reason": "Refund for bad call"}))
    assert balance(api, patient) == 20                           # 20 - 10 (call) + 10 (refund); a waiting request holds nothing
    api.ok(api.post(f"/admin/users/{patient_id}/suspend", admin, {"suspended": True, "reason": "test"}))
    api.ok(api.post(f"/admin/users/{patient_id}/suspend", admin, {"suspended": False, "reason": "test"}))
    assert current(api, patient) is None                         # suspension cancelled the waiting request
    status, body = api.post("/admin/wallet/adjust", admin, {"user_id": patient_id, "amount_taka": -500, "reason": "oops"})
    assert status == 402                                         # a balance can never go below zero


def test_live_events_over_websocket(api):
    patient, _ = api.patient()
    doctor, _ = api.doctor()
    with api.client.websocket_connect(f"/ws?token={doctor}") as doctor_ws, api.client.websocket_connect(f"/ws?token={patient}") as patient_ws:
        c = api.ok(api.post("/match", patient, {"problem": "fever"}))
        offer = doctor_ws.receive_json()
        assert offer["event"] == "offer" and offer["data"]["id"] == c["id"]
        assert patient_ws.receive_json()["data"]["status"] == "offered"
        api.ok(api.post(f"/consultations/{c['id']}/accept", doctor))
        update = patient_ws.receive_json()
        assert update["event"] == "consultation" and update["data"]["status"] == "active" and update["data"]["doctor"]["verified"]


def test_waiting_patient_is_told_live_when_a_doctor_is_ready(api):
    patient, _ = api.patient()
    c = api.ok(api.post("/match", patient, {"problem": "fever"}))
    with api.client.websocket_connect(f"/ws?token={patient}") as patient_ws:
        doctor, _ = api.doctor()
        assert patient_ws.receive_json()["data"]["status"] == "offered"
        api.ok(api.post(f"/consultations/{c['id']}/accept", doctor))
        ready = patient_ws.receive_json()
        assert ready["event"] == "consultation" and ready["data"]["status"] == "ready" and ready["data"]["join_by"]


def test_ledger_always_matches_balances(api):
    patient, patient_id = api.patient(balance=100)
    doctor, doctor_id = api.doctor()
    for seconds in (1, 45, 700, 2):
        cid = connect(api, patient, doctor)
        api.wait(seconds, doctor)
        api.post(f"/consultations/{cid}/end", patient)
        api.clock.forward(5)
        api.post("/doctor/online", doctor)
    for user_id in (patient_id, doctor_id):
        total = core.one("SELECT COALESCE(SUM(amount_poisha), 0) AS s FROM wallet_transactions WHERE user_id = ?", user_id)["s"]
        assert total == core.one("SELECT balance_poisha FROM users WHERE id = ?", user_id)["balance_poisha"]
    assert not core.one("SELECT 1 FROM consultations WHERE status NOT IN ('ended', 'skipped')")
    assert not core.one("SELECT 1 FROM consultations WHERE held = 1")
