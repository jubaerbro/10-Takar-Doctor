import io
import pytest
from starlette.testclient import TestClient

from app import core
from app.core import Config
from app.main import create_app


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def forward(self, seconds):
        self.t += seconds


class Api:
    """Tiny helper around the test client: api.post(path, token, json) -> (status, body)."""

    def __init__(self, client, clock):
        self.client, self.clock = client, clock
        self._n = 0

    def call(self, method, path, token=None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = self.client.request(method, path, headers=headers, **kw)
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content)

    def get(self, path, token=None, **kw):
        return self.call("GET", path, token, **kw)

    def post(self, path, token=None, json=None, **kw):
        return self.call("POST", path, token, json=json, **kw)

    def put(self, path, token=None, json=None, **kw):
        return self.call("PUT", path, token, json=json, **kw)

    def ok(self, result):
        status, body = result
        assert status == 200, body
        return body

    def wait(self, seconds, *doctors):
        """Move time forward while the given doctors' apps keep sending heartbeats, as the real app does."""
        while seconds > 0:
            step = min(30, seconds)
            self.clock.forward(step)
            seconds -= step
            for token in doctors:
                self.post("/doctor/heartbeat", token)

    def phone(self):
        self._n += 1
        return f"017{self._n:08d}"

    def login(self, role, phone=None):
        phone = phone or self.phone()
        code = self.ok(self.post("/auth/otp/request", json={"phone": phone}))["dev_code"]
        body = self.ok(self.post("/auth/otp/verify", json={"phone": phone, "code": code, "role": role}))
        return body["token"], body["me"]["id"]

    def admin(self):
        return self.ok(self.post("/admin/login", json={"email": "boss@example.com", "password": "correct-horse-battery"}))["token"]

    def patient(self, balance=100, age=30):
        token, uid = self.login("patient")
        self.ok(self.post("/me/consent", token, {"version": self.ok(self.get("/config"))["terms_version"]}))
        self.ok(self.put("/patient/profile", token, {"real_name": "Rahim Uddin", "age": age, "gender": "male", "district": "Dhaka"}))
        if balance:
            if balance < 20:
                self.ok(self.put("/admin/settings", self.admin(), {"min_topup_taka": 1}))
            t = self.ok(self.post("/wallet/topups", token, {"amount_taka": balance, "method": "bkash"}))
            self.ok(self.post(f"/dev/topups/{t['id']}/confirm", token))
        return token, uid

    def doctor_pending(self, specialty="general", gender="female", languages=("bn",)):
        token, uid = self.login("doctor")
        self.ok(self.post("/me/consent", token, {"version": "1"}))
        self.ok(self.put("/doctor/profile", token, {
            "real_name": "Dr Nasrin Akter", "bmdc_number": "A-12345", "gender": gender, "specialty": specialty,
            "experience_years": 8, "languages": list(languages)}))
        for doc_type in ("bmdc_certificate", "degree_certificate", "nid", "selfie"):
            self.ok(self.post("/doctor/documents", token, data={"doc_type": doc_type},
                              files={"file": ("doc.png", io.BytesIO(b"\x89PNG fake"), "image/png")}))
        self.ok(self.post("/doctor/submit", token))
        return token, uid

    def doctor(self, designation="medical_officer", online=True, **kw):
        token, uid = self.doctor_pending(**kw)
        self.ok(self.post(f"/admin/doctors/{uid}/review", self.admin(), {"action": "approve", "designation": designation}))
        if online:
            self.ok(self.post("/doctor/online", token))
        return token, uid


@pytest.fixture
def api(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(core, "now", clock)
    app = create_app(Config(database_path=str(tmp_path / "t.db"), upload_dir=str(tmp_path / "up"), dev_mode=True,
                            admin_email="boss@example.com", admin_password="correct-horse-battery"),
                     background_sweep=False)
    with TestClient(app) as client:
        yield Api(client, clock)


@pytest.fixture(autouse=True)
def money_always_adds_up(api):
    """After every test: each balance equals the sum of its ledger rows, and money is held only for live calls."""
    yield
    for user in core.rows("SELECT id, balance_poisha FROM users"):
        total = core.one("SELECT COALESCE(SUM(amount_poisha), 0) AS s FROM wallet_transactions WHERE user_id = ?", user["id"])["s"]
        assert total == user["balance_poisha"]
        held = core.one("SELECT COALESCE(SUM(hold_poisha), 0) AS s FROM consultations WHERE patient_id = ? AND held = 1", user["id"])["s"]
        net_holds = core.one("SELECT COALESCE(SUM(-amount_poisha), 0) AS s FROM wallet_transactions "
                             "WHERE user_id = ? AND type IN ('hold', 'release')", user["id"])["s"]
        assert held == net_holds
    assert not core.one("SELECT 1 FROM consultations WHERE held = 1 AND status NOT IN ('offered', 'ready', 'active')")
