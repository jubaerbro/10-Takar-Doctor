def test_otp_login_and_roles(api):
    status, body = api.post("/auth/otp/request", json={"phone": "12345"})
    assert status == 422 and body["error"]["code"] == "invalid_phone"

    code = api.ok(api.post("/auth/otp/request", json={"phone": "01711111111"}))["dev_code"]
    status, body = api.post("/auth/otp/request", json={"phone": "+8801711111111"})
    assert status == 429                                     # same number, asked again too soon
    wrong = "000000" if code != "000000" else "111111"
    status, body = api.post("/auth/otp/verify", json={"phone": "01711111111", "code": wrong, "role": "patient"})
    assert status == 400 and body["error"]["code"] == "otp_wrong"
    body = api.ok(api.post("/auth/otp/verify", json={"phone": "01711111111", "code": code, "role": "patient"}))
    assert body["is_new"] and body["me"]["phone"] == "+8801711111111" and body["me"]["consent_needed"]
    token = body["token"]
    status, _ = api.post("/auth/otp/verify", json={"phone": "01711111111", "code": code, "role": "patient"})
    assert status == 400                                     # a code works once

    assert api.get("/me")[0] == 401
    assert api.get("/me", token)[0] == 200
    assert api.post("/doctor/online", token)[0] == 403       # a patient cannot use doctor routes
    assert api.get("/admin/settings", token)[0] == 403


def test_otp_expires_and_locks(api):
    code = api.ok(api.post("/auth/otp/request", json={"phone": "01722222222"}))["dev_code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        api.post("/auth/otp/verify", json={"phone": "01722222222", "code": wrong, "role": "patient"})
    status, body = api.post("/auth/otp/verify", json={"phone": "01722222222", "code": code, "role": "patient"})
    assert status == 429 and body["error"]["code"] == "otp_locked"

    api.clock.forward(61)
    code = api.ok(api.post("/auth/otp/request", json={"phone": "01722222222"}))["dev_code"]
    api.clock.forward(301)
    status, body = api.post("/auth/otp/verify", json={"phone": "01722222222", "code": code, "role": "patient"})
    assert status == 400 and body["error"]["code"] == "otp_expired"


def test_minor_needs_guardian(api):
    token, _ = api.login("patient")
    profile = {"real_name": "Mina", "age": 12, "gender": "female", "district": "Khulna"}
    status, body = api.put("/patient/profile", token, profile)
    assert status == 422 and body["error"]["code"] == "guardian_required"
    api.ok(api.put("/patient/profile", token, {**profile, "guardian_name": "Salma Begum", "guardian_relation": "mother"}))


def test_doctor_verification(api):
    token, uid = api.login("doctor")
    assert api.post("/doctor/submit", token)[1]["error"]["code"] == "profile_missing"
    api.ok(api.put("/doctor/profile", token, {"real_name": "Dr Karim", "bmdc_number": "A-999", "gender": "male",
                                              "specialty": "skin", "experience_years": 12, "languages": ["bn", "en"]}))
    assert api.post("/doctor/submit", token)[1]["error"]["code"] == "consent_needed"
    api.ok(api.post("/me/consent", token, {"version": "1"}))
    assert api.post("/doctor/submit", token)[1]["error"]["code"] == "documents_missing"
    status, body = api.post("/doctor/documents", token, data={"doc_type": "nid"}, files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert status == 422 and body["error"]["code"] == "invalid_file_type"
    status, body = api.post("/doctor/documents", token, data={"doc_type": "nid"}, files={"file": ("x.png", b"MZ not a png", "image/png")})
    assert status == 422 and body["error"]["code"] == "invalid_file_type"   # wrong contents under an allowed name

    token, uid = api.doctor_pending()
    assert api.post("/doctor/online", token)[1]["error"]["code"] == "not_verified"
    assert api.put("/doctor/profile", token, {"real_name": "Dr X", "bmdc_number": "A-1", "gender": "male", "specialty": "skin",
                                              "experience_years": 1, "languages": ["bn"]})[0] == 409   # locked while pending

    admin = api.admin()
    pending = api.ok(api.get("/admin/doctors?status=pending", admin))["doctors"]
    assert [d["id"] for d in pending] == [uid] and len(pending[0]["documents"]) == 4
    status, content = api.get(f"/admin/documents/{pending[0]['documents'][0]['id']}/file", admin)
    assert status == 200 and content == b"\x89PNG fake"
    assert api.get(f"/admin/documents/{pending[0]['documents'][0]['id']}/file", token)[0] == 403   # doctors cannot read the admin file route

    assert api.post(f"/admin/doctors/{uid}/review", admin, {"action": "request_changes"})[1]["error"]["code"] == "reason_required"
    api.ok(api.post(f"/admin/doctors/{uid}/review", admin, {"action": "request_changes", "reason": "NID photo is blurred"}))
    me = api.ok(api.get("/me", token))
    assert me["profile"]["status"] == "changes_requested" and me["profile"]["review_reason"] == "NID photo is blurred"
    api.ok(api.post("/doctor/submit", token))

    assert api.post(f"/admin/doctors/{uid}/review", admin, {"action": "approve"})[1]["error"]["code"] == "invalid_designation"
    api.ok(api.post(f"/admin/doctors/{uid}/review", admin, {"action": "approve", "designation": "professor"}))
    assert api.ok(api.post("/doctor/online", token)) == {"online": True}
    actions = [e["action"] for e in api.ok(api.get("/admin/audit", admin))["entries"]]
    assert "doctor_approve" in actions and "document_viewed" in actions


def test_registration_fee_charged_once(api):
    admin = api.admin()
    api.ok(api.put("/admin/settings", admin, {"doctor_registration_fee_taka": 200}))
    token, uid = api.login("doctor")
    api.ok(api.post("/me/consent", token, {"version": "1"}))
    api.ok(api.put("/doctor/profile", token, {"real_name": "Dr Karim", "bmdc_number": "A-999", "gender": "male",
                                              "specialty": "skin", "experience_years": 12, "languages": ["bn"]}))
    for doc_type in ("bmdc_certificate", "degree_certificate", "nid", "selfie"):
        api.ok(api.post("/doctor/documents", token, data={"doc_type": doc_type}, files={"file": ("d.png", b"\x89PNG x", "image/png")}))
    assert api.post("/doctor/submit", token)[1]["error"]["code"] == "insufficient_balance"
    t = api.ok(api.post("/wallet/topups", token, {"amount_taka": 500, "method": "nagad"}))
    api.ok(api.post(f"/dev/topups/{t['id']}/confirm", token))
    api.ok(api.post(f"/dev/topups/{t['id']}/confirm", token))          # confirming twice credits once
    assert api.ok(api.post("/doctor/submit", token))["balance_taka"] == 300
    api.ok(api.post(f"/admin/doctors/{uid}/review", admin, {"action": "request_changes", "reason": "retake selfie"}))
    assert api.ok(api.post("/doctor/submit", token))["balance_taka"] == 300   # not charged again


def test_admin_roles_and_settings(api):
    admin = api.admin()
    assert api.post("/admin/login", json={"email": "boss@example.com", "password": "wrong"})[0] == 401
    api.ok(api.post("/admin/admins", admin, {"email": "v@example.com", "password": "long-enough-pw", "admin_role": "verifier"}))
    verifier = api.ok(api.post("/admin/login", json={"email": "v@example.com", "password": "long-enough-pw"}))["token"]
    assert api.put("/admin/settings", verifier, {"instant_call_price_taka": 1})[0] == 403
    assert api.post("/admin/wallet/adjust", verifier, {"user_id": "x", "amount_taka": 5, "reason": "test"})[0] == 403

    assert api.put("/admin/settings", admin, {"instant_call_price_taka": -1})[0] == 422
    assert api.put("/admin/settings", admin, {"nonsense": 1})[0] == 422
    assert api.put("/admin/settings", admin, {"doctor_pay": {"professor": {"per_call_taka": 1}}})[0] == 422
    api.ok(api.put("/admin/settings", admin, {"instant_call_price_taka": 12, "instant_call_max_minutes": 8}))
    config = api.ok(api.get("/config"))
    assert config["instant_call_price_taka"] == 12 and config["instant_call_max_minutes"] == 8
    assert config["test_mode"] is True
    entry = api.ok(api.get("/admin/audit", admin))["entries"]
    assert {"old": 10, "new": 12} in [e["detail"] for e in entry]


def test_admin_login_locks_after_wrong_passwords(api):
    for _ in range(5):
        assert api.post("/admin/login", json={"email": "boss@example.com", "password": "guess"})[0] == 401
    assert api.post("/admin/login", json={"email": "boss@example.com", "password": "correct-horse-battery"})[0] == 429
    api.clock.forward(901)
    api.admin()
