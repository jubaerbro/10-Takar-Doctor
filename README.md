# 10 Takar Doctor

Instant, low-cost video consultations with verified doctors in Bangladesh.

| Folder | What it is | Status |
| --- | --- | --- |
| `backend/` | The server the apps talk to | Working, with automated tests |
| `app/` | Flutter mobile app, patient side | First version. Doctor side and live video not built yet |
| `.github/workflows/` | Builds the Android test app and runs the backend tests on every push | |

The web admin panel is not started.

## Mobile app

Patient flow: phone login, consent, profile, wallet, find a doctor with filters and an emergency check,
searching, the call screen (doctor's name, BMDC number, countdown, end, next doctor, report), rating and
the doctor's written advice. Bangla by default, English by a switch.

Every push to `main` builds a test APK: open the repository's **Actions** tab, pick the latest
**Build Android app** run and download `ten-takar-doctor-test-apk`. The app needs a hosted backend:
set the repository variable `API_BASE_URL` (Settings → Secrets and variables → Actions → Variables)
to its `https://` address.

Not in the app yet: the video picture (waiting for a LiveKit account), real payments, doctor mode, bookings.

## What the backend does today

| Area | Status |
| --- | --- |
| Phone + OTP login for patients and doctors | Working. Needs an SMS provider plugged in (`send_sms` in `app/accounts.py`). |
| Admin login with three admin roles | Working, with lockout after 5 wrong passwords. Two-factor login not built yet. |
| Admin-editable settings (prices, pay, fees, limits) with audit log | Working |
| Doctor profile, document upload, verification queue, approve / reject / ask for changes | Working |
| Wallet: top-up, hold, charge, refund, doctor earnings, admin adjustments and payouts | Working. Needs a real bKash/Nagad gateway (`create_topup` in `app/wallet.py`). |
| Instant consult: filters, queue, offer, accept / decline / timeout, 10-minute cap, free 3-second skip | Working |
| Waiting requests: saved when no matching doctor is free, offered to the next one who comes online | Working. Phone notifications not built yet. |
| Doctor pay by designation: per call + per minute | Working |
| Advice notes (no medicine field), ratings, reports, suspensions | Working |
| Video rooms | Join tokens are issued in LiveKit's format. Not yet tried against a LiveKit server. |
| Bookings, Pro plans, discounts and promo codes | Not built yet |

## Run it

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # then edit it
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --factory --host 0.0.0.0 --port 8000
```

For a quick local try without SMS or a payment gateway, set `DEV_MODE=1`: OTP codes come back in the API
response and `/dev/topups/{id}/confirm` fakes a payment. **Never use `DEV_MODE=1` in production.**

Run the tests:

```bash
cd backend && python -m pytest -q
```

## Data

The database is SQLite (one file, `data/app.db`), which is enough for a pilot on one server.
The schema in `app/schema.sql` is written in portable SQL so it can move to PostgreSQL before the app grows.
Money is stored in whole poisha; the API speaks taka. Times are Unix seconds (UTC).

## API in brief

All requests and responses are JSON. Send the login token as `Authorization: Bearer <token>`.
Errors look like `{"error": {"code": "...", "message": "..."}}`.

**Everyone**

| Method and path | Purpose |
| --- | --- |
| `GET /config` | Prices, call length, specialties, terms version (for display) |
| `POST /auth/otp/request` | Send a login code to a phone |
| `POST /auth/otp/verify` | Check the code; returns the login token. `role` is `patient` or `doctor` |
| `GET /me` | My account, profile, balance |
| `POST /me/consent` | Accept the current terms |
| `GET /wallet` · `POST /wallet/topups` | Balance and history · start a top-up |
| `GET /consultations/current` | The call in progress, if any (poll every 1–2 s, or listen on `/ws`) |
| `GET /consultations` · `GET /consultations/{id}` | History · one consultation |
| `POST /consultations/{id}/end` | End the call (for a patient this is also "next doctor") |
| `POST /consultations/{id}/report` | Report the other person |
| `WS /ws?token=...` | Live events: `offer`, `offer_closed`, `consultation`, `verification` |

**Patient**

| Method and path | Purpose |
| --- | --- |
| `PUT /patient/profile` | Name, age, gender, district (guardian needed under 18) |
| `POST /match` | Find a doctor: `problem`, optional `specialty`, `language`, `doctor_gender` |
| `POST /consultations/{id}/cancel` | Stop searching or cancel a waiting request; any held money comes back |
| `POST /consultations/{id}/join` | Join when a doctor has picked up your waiting request (status `ready`) |
| `POST /consultations/{id}/rating` | Rate the doctor, 1–5 |

**Doctor**

| Method and path | Purpose |
| --- | --- |
| `PUT /doctor/profile` · `POST /doctor/documents` · `POST /doctor/submit` | Verification file |
| `POST /doctor/online` · `/doctor/offline` · `/doctor/heartbeat` | Availability (heartbeat every 30 s while online) |
| `POST /consultations/{id}/accept` · `/decline` | Answer an offered call |
| `PUT /consultations/{id}/advice-note` | Written advice after the call |
| `GET /doctor/earnings` | Today, last 7 days, all time |

**Admin**

| Method and path | Purpose |
| --- | --- |
| `POST /admin/login` · `POST /admin/admins` | Log in · create another admin |
| `GET /admin/dashboard` | Live counts and today's income, doctor pay and margin |
| `GET /admin/settings` · `PUT /admin/settings` | Read and change prices, pay rates, fees, limits |
| `GET /admin/doctors?status=pending` · `GET /admin/doctors/{id}` | Verification queue |
| `GET /admin/documents/{id}/file` | View an uploaded document (logged) |
| `POST /admin/doctors/{id}/review` | Approve (sets designation), reject, or ask for changes |
| `POST /admin/users/{id}/suspend` | Suspend or restore an account |
| `POST /admin/wallet/adjust` | Refund, correction or doctor payout |
| `GET /admin/reports` · `POST /admin/reports/{id}/resolve` | Handle reports |
| `GET /admin/audit` | Every admin action |

## How a 10 taka call is charged

1. `POST /match` holds the call price from the patient's wallet.
2. The longest-waiting free doctor who matches the filters is offered the call and has 15 seconds to accept.
3. On accept, the price, the 10-minute cap and the doctor's pay rates are copied onto the consultation.
4. When the call ends:
   - within 3 seconds: free. The hold is returned and the doctor is not paid. Patients get 10 free skips a day.
   - otherwise: the patient is charged the price; the doctor earns `per call + per minute × minutes` for their designation.
5. The server ends any call that reaches the cap.

## Waiting requests

If no doctor matching the patient's filters is free, the search is not refused. It becomes a **waiting request**:

1. The request is saved with status `waiting`. No money is held.
2. When a matching doctor comes online or finishes a call, the oldest waiting request is offered to them, marked `from_waiting_request`.
3. If the doctor accepts, the price is held and the status becomes `ready`. The patient has 60 seconds to join.
   The patient app joins by itself if it is open.
4. On joining, the call starts and the 10 minutes begin.
5. If the patient does not join in time, the money is returned and the request waits for another doctor. Two misses cancel it.
6. A request that nobody picks up expires after 6 hours.

The patient is only told while the app is open. Phone notifications for a closed app are not built yet.

Every number above is an admin setting.
