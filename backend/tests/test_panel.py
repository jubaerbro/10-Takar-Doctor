def test_panel_is_served_with_strict_headers(api):
    r = api.client.get("/panel/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "/panel/app.js" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert api.client.get("/panel/app.js").headers["content-type"].startswith("text/javascript")
    assert api.client.get("/panel/style.css").status_code == 200
    assert api.client.get("/panel/../core.py").status_code == 404
    assert api.client.get("/panel/schema.sql").status_code == 404
    assert api.client.get("/panel", follow_redirects=False).status_code in (302, 307)


def test_admin_lookups_for_the_panel(api):
    admin = api.admin()
    assert api.ok(api.get("/admin/me", admin))["admin_role"] == "super_admin"
    assert [a["email"] for a in api.ok(api.get("/admin/admins", admin))["admins"]] == ["boss@example.com"]

    patient, patient_id = api.patient(balance=50)
    doctor, doctor_id = api.doctor(designation="consultant")
    users = api.ok(api.get("/admin/users", admin))["users"]
    assert {u["id"] for u in users} == {patient_id, doctor_id}
    found = api.ok(api.get("/admin/users?q=Nasrin", admin))["users"]
    assert len(found) == 1 and found[0]["role"] == "doctor" and found[0]["doctor_status"] == "approved"
    assert api.ok(api.get("/admin/users?q=nobody-by-this-name", admin))["users"] == []

    detail = api.ok(api.get(f"/admin/users/{patient_id}", admin))
    assert detail["balance_taka"] == 50 and detail["name"] == "Rahim Uddin" and detail["transactions"][0]["type"] == "topup"
    assert api.get("/admin/users/does-not-exist", admin)[0] == 404
    assert api.get("/admin/users", patient)[0] == 403

    api.ok(api.post("/admin/admins", admin, {"email": "v@example.com", "password": "long-enough-pw", "admin_role": "verifier"}))
    verifier = api.ok(api.post("/admin/login", json={"email": "v@example.com", "password": "long-enough-pw"}))["token"]
    assert api.get("/admin/users", verifier)[0] == 403          # verifiers check doctors; they do not see wallets
    assert api.get("/admin/admins", verifier)[0] == 403

    c = api.ok(api.post("/match", patient, {"problem": "fever"}))
    api.ok(api.post(f"/consultations/{c['id']}/accept", doctor))
    api.wait(60, doctor)
    api.ok(api.post(f"/consultations/{c['id']}/end", patient))
    api.ok(api.post(f"/consultations/{c['id']}/report", patient, {"reason": "abuse"}))
    report = api.ok(api.get("/admin/reports", admin))["reports"][0]
    assert report["target"]["name"] == "Dr Nasrin Akter" and report["reporter"]["id"] == patient_id
    assert report["consultation"]["problem"] == "fever"
