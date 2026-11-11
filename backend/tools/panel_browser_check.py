"""Drives the admin panel in a real browser against a throwaway server.

Not part of the normal test run (it needs Playwright and Chromium):
    pip install playwright requests && playwright install chromium
    cd backend && python tools/panel_browser_check.py
"""
import os, struct, subprocess, sys, tempfile, time, zlib
import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8791"
TMP = tempfile.mkdtemp(prefix="panelcheck-")
env = dict(os.environ, DEV_MODE="1", DATABASE_PATH=TMP + "/app.db", UPLOAD_DIR=TMP + "/up",
           ADMIN_EMAIL="owner@example.com", ADMIN_PASSWORD="a-long-test-password")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"): env.pop(k, None)
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--factory", "--port", "8791", "--log-level", "warning"], env=env)
S = requests.Session(); S.trust_env = False

def png(color):
    w = h = 64
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

def call(method, path, token=None, **kw):
    r = S.request(method, BASE + path, headers={"Authorization": f"Bearer {token}"} if token else {}, **kw)
    assert r.status_code == 200, (path, r.status_code, r.text)
    return r.json()

def login(phone, role):
    code = call("POST", "/auth/otp/request", json={"phone": phone})["dev_code"]
    return call("POST", "/auth/otp/verify", json={"phone": phone, "code": code, "role": role})["token"]

try:
    for _ in range(50):
        try:
            if S.get(BASE + "/health").status_code == 200: break
        except Exception: time.sleep(0.2)
    doc = login("01711000001", "doctor")
    call("POST", "/me/consent", doc, json={"version": "1"})
    call("PUT", "/doctor/profile", doc, json={"real_name": "Dr Farhana Islam", "bmdc_number": "A-55501", "gender": "female",
                                              "specialty": "skin", "experience_years": 9, "languages": ["bn", "en"]})
    for i, t in enumerate(("bmdc_certificate", "degree_certificate", "nid", "selfie")):
        call("POST", "/doctor/documents", doc, data={"doc_type": t}, files={"file": ("d.png", png((40 + 50 * i, 120, 200 - 40 * i)), "image/png")})
    call("POST", "/doctor/submit", doc)
    pat = login("01711000002", "patient")
    call("POST", "/me/consent", pat, json={"version": "1"})
    call("PUT", "/patient/profile", pat, json={"real_name": "Karim Hossain", "age": 41, "gender": "male", "district": "Sylhet"})

    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 850})
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("dialog", lambda d: d.accept())
        page.goto(BASE + "/panel")
        assert "TEST MODE" in page.inner_text(".test-strip")
        page.fill("input[type=email]", "owner@example.com"); page.fill("input[type=password]", "wrong-password"); page.click("button[type=submit]")
        page.wait_for_selector(".toast.error"); assert "Wrong email or password" in page.inner_text("#toast")
        page.fill("input[type=password]", "a-long-test-password"); page.click("button[type=submit]")
        page.wait_for_selector("text=Doctors waiting for verification")
        assert page.locator(".toast.error").count() == 0      # the wrong-password message is gone after logging in
        assert page.inner_text(".card >> nth=0").startswith("1")
        page.screenshot(path=TMP + "/1-dashboard.png")

        page.click("button[data-view=doctors]"); page.click("tr.clickable >> text=Dr Farhana Islam")
        page.wait_for_selector(".doc img >> nth=3")
        assert page.eval_on_selector_all(".doc img", "els => els.every(e => e.complete && e.naturalWidth === 64)")
        page.screenshot(path=TMP + "/2-doctor.png", full_page=True)
        page.click(".doc img >> nth=0"); page.wait_for_selector(".overlay img"); page.click(".overlay")
        page.click("button:has-text('Approve')")            # no designation chosen: must be refused
        page.wait_for_selector(".toast.error:has-text('designation')")
        page.select_option("select >> nth=0", "consultant"); page.click("button:has-text('Approve')")
        page.wait_for_selector("h2:has-text('Change designation')")
        assert call("GET", "/me", doc)["profile"]["status"] == "approved" and call("GET", "/me", doc)["profile"]["designation"] == "consultant"

        page.click("button[data-view=settings]"); page.wait_for_selector("text=Doctor pay and booking price")
        first = page.locator("table >> nth=0").locator("input >> nth=0")
        assert first.input_value() == "10"
        margin_before = page.locator("table >> nth=1").locator("tbody tr >> nth=0").locator("td >> nth=4").inner_text()
        first.fill("12")
        margin_after = page.locator("table >> nth=1").locator("tbody tr >> nth=0").locator("td >> nth=4").inner_text()
        assert (margin_before, margin_after) == ("−৳30", "−৳28"), (margin_before, margin_after)
        page.screenshot(path=TMP + "/3-settings.png", full_page=True)
        page.click("button:has-text('Save changes')"); page.wait_for_selector("text=Settings saved")
        assert call("GET", "/config")["instant_call_price_taka"] == 12
        first = page.locator("table >> nth=0").locator("input >> nth=0"); first.fill("")
        page.click("button:has-text('Save changes')"); page.wait_for_selector(".toast.error:has-text('filled in')")
        assert call("GET", "/config")["instant_call_price_taka"] == 12   # an empty box is refused, not saved as 0

        page.click("button[data-view=users]"); page.fill("input[type=search]", "Karim"); page.click("button:has-text('Search')")
        page.click("tr.clickable >> text=Karim Hossain"); page.wait_for_selector("h2:has-text('Wallet')")
        page.fill("input[type=number]", "25"); page.locator("input[placeholder^='Reason']").first.fill("Test credit"); page.click("button:has-text('Apply')")
        page.wait_for_selector("td:has-text('Test credit')")
        assert call("GET", "/wallet", pat)["balance_taka"] == 25
        page.screenshot(path=TMP + "/4-user.png", full_page=True)

        page.click("button[data-view=reports]"); page.wait_for_selector("h1:has-text('Reports')")
        page.click("button[data-view=audit]"); page.wait_for_selector("td:has-text('setting changed')")
        assert page.locator("td:has-text('owner@example.com')").count() >= 3
        page.click("button[data-view=admins]"); page.wait_for_selector("td:has-text('owner@example.com')")
        page.fill("input[type=email]", "check@example.com"); page.fill("input[type=password]", "another-long-pass"); page.click("button:has-text('Create admin')")
        page.wait_for_selector("td:has-text('check@example.com')")

        # a verifier sees only what the role allows
        page.click("button:has-text('Log out')"); page.fill("input[type=email]", "check@example.com"); page.fill("input[type=password]", "another-long-pass"); page.click("button[type=submit]")
        page.wait_for_selector("text=Doctors waiting for verification")
        assert page.eval_on_selector_all(".nav button[data-view]", "els => els.map(e => e.dataset.view)") == ["dashboard", "doctors"]
        browser.close()
    real = [x for x in problems if "401" not in x]   # the wrong-password attempt logs a 401 by design
    print("browser problems:", real or "none")
    print("PANEL TEST PASSED")
finally:
    server.terminate()
