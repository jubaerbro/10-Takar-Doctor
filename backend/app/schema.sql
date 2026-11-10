-- 10 Takar Doctor: database schema.
-- Money is stored as whole poisha (1 taka = 100 poisha). Times are Unix seconds (UTC).
-- Written in a portable SQL subset so it can move from SQLite to PostgreSQL.

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    phone           TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('patient', 'doctor')),
    alias           TEXT NOT NULL,
    language        TEXT NOT NULL DEFAULT 'bn',
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    consent_version TEXT,
    consent_at      REAL,
    balance_poisha  INTEGER NOT NULL DEFAULT 0 CHECK (balance_poisha >= 0),
    created_at      REAL NOT NULL,
    UNIQUE (phone, role)
);

CREATE TABLE IF NOT EXISTS otp_codes (
    phone      TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS otp_phone ON otp_codes (phone, created_at);

CREATE TABLE IF NOT EXISTS admins (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    admin_role    TEXT NOT NULL CHECK (admin_role IN ('super_admin', 'verifier', 'support_finance')),
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS patient_profiles (
    user_id           TEXT PRIMARY KEY REFERENCES users (id),
    real_name         TEXT NOT NULL,
    age               INTEGER NOT NULL,
    gender            TEXT NOT NULL,
    district          TEXT NOT NULL,
    guardian_name     TEXT,
    guardian_relation TEXT,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS doctor_profiles (
    user_id          TEXT PRIMARY KEY REFERENCES users (id),
    real_name        TEXT NOT NULL,
    bmdc_number      TEXT NOT NULL,
    gender           TEXT NOT NULL,
    specialty        TEXT NOT NULL,
    designation      TEXT,              -- pay tier; set by admin at approval
    experience_years INTEGER NOT NULL DEFAULT 0,
    languages        TEXT NOT NULL DEFAULT '["bn"]',   -- JSON list
    status           TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'pending', 'approved', 'rejected', 'changes_requested')),
    review_reason    TEXT,
    reviewed_by      TEXT,
    reviewed_at      REAL,
    online           INTEGER NOT NULL DEFAULT 0,
    last_seen        REAL NOT NULL DEFAULT 0,
    last_assigned_at REAL NOT NULL DEFAULT 0,
    rating_sum       INTEGER NOT NULL DEFAULT 0,
    rating_count     INTEGER NOT NULL DEFAULT 0,
    updated_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS doctor_documents (
    id           TEXT PRIMARY KEY,
    doctor_id    TEXT NOT NULL REFERENCES users (id),
    doc_type     TEXT NOT NULL,
    file_name    TEXT NOT NULL,     -- name on disk inside the private upload folder
    content_type TEXT NOT NULL,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL REFERENCES users (id),
    type                 TEXT NOT NULL,   -- topup, hold, release, charge, earning, fee, adjust, payout
    amount_poisha        INTEGER NOT NULL,  -- signed change to the balance
    balance_after_poisha INTEGER NOT NULL,
    ref_type             TEXT,
    ref_id               TEXT,
    note                 TEXT,
    created_at           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS wallet_user ON wallet_transactions (user_id, created_at);

CREATE TABLE IF NOT EXISTS topups (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users (id),
    amount_poisha INTEGER NOT NULL,
    method        TEXT NOT NULL,
    gateway_ref   TEXT,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed')),
    created_at    REAL NOT NULL,
    paid_at       REAL
);

CREATE TABLE IF NOT EXISTS consultations (
    id                    TEXT PRIMARY KEY,
    type                  TEXT NOT NULL DEFAULT 'instant',
    patient_id            TEXT NOT NULL REFERENCES users (id),
    doctor_id             TEXT REFERENCES users (id),
    status                TEXT NOT NULL CHECK (status IN
                          ('searching', 'offered', 'waiting', 'ready', 'active', 'ended', 'skipped', 'cancelled', 'no_doctor')),
    problem               TEXT NOT NULL,
    filters               TEXT NOT NULL DEFAULT '{}',   -- JSON
    declined              TEXT NOT NULL DEFAULT '[]',   -- JSON list of doctor ids that passed
    -- values copied from admin settings when the call was made, so later changes never alter history
    hold_poisha           INTEGER NOT NULL,          -- the call price when the patient asked
    held                  INTEGER NOT NULL DEFAULT 0, -- 1 while that amount is held from the patient's wallet
    -- waiting request: set when no matching doctor was free and the request was parked
    waiting_since         REAL,
    ready_at              REAL,                      -- a doctor picked the request up; waiting for the patient to join
    missed_joins          INTEGER NOT NULL DEFAULT 0,
    max_seconds           INTEGER,
    free_skip_seconds     INTEGER,
    pay_per_call_poisha   INTEGER,
    pay_per_minute_poisha INTEGER,
    designation           TEXT,
    -- outcome
    price_charged_poisha  INTEGER NOT NULL DEFAULT 0,
    doctor_pay_poisha     INTEGER NOT NULL DEFAULT 0,
    duration_seconds      INTEGER,
    ended_by              TEXT,
    rating                INTEGER,
    rating_comment        TEXT,
    created_at            REAL NOT NULL,
    offered_at            REAL,
    started_at            REAL,
    ended_at              REAL
);
CREATE INDEX IF NOT EXISTS consult_patient ON consultations (patient_id, created_at);
CREATE INDEX IF NOT EXISTS consult_doctor ON consultations (doctor_id, created_at);
CREATE INDEX IF NOT EXISTS consult_status ON consultations (status);

CREATE TABLE IF NOT EXISTS advice_notes (
    consultation_id TEXT PRIMARY KEY REFERENCES consultations (id),
    assessment      TEXT NOT NULL,
    self_care       TEXT NOT NULL DEFAULT '',
    tests           TEXT NOT NULL DEFAULT '',
    referral        TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id              TEXT PRIMARY KEY,
    consultation_id TEXT NOT NULL REFERENCES consultations (id),
    reporter_id     TEXT NOT NULL REFERENCES users (id),
    target_id       TEXT NOT NULL REFERENCES users (id),
    reason          TEXT NOT NULL,
    details         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolution      TEXT,
    resolved_by     TEXT,
    created_at      REAL NOT NULL,
    resolved_at     REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,     -- JSON
    updated_by TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         TEXT PRIMARY KEY,
    admin_id   TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT NOT NULL DEFAULT '{}',   -- JSON
    created_at REAL NOT NULL
);
