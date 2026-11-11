'use strict';
/* 10 Takar Doctor admin panel. Plain JavaScript, no outside libraries.
   Everything shown comes from the /admin API; text is always inserted as text, never as HTML. */

const S = { token: sessionStorage.getItem('admin_token'), me: null, config: null, view: 'dashboard' };
const $app = document.getElementById('app');
const $toast = document.getElementById('toast');

const DESIGNATIONS = {
  professor: 'Professor', associate_professor: 'Associate Professor', assistant_professor: 'Assistant Professor',
  consultant: 'Consultant', medical_officer: 'Medical Officer',
};
const DOC_NAMES = { bmdc_certificate: 'BMDC certificate', degree_certificate: 'Degree certificate', nid: 'National ID', selfie: 'Selfie' };

// ----------------------------------------------------------------------------- helpers

/** Build a DOM element. Strings become text nodes, so data can never be read as HTML. */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'value') el.value = value;
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

async function api(method, path, body) {
  const headers = {};
  if (S.token) headers.Authorization = 'Bearer ' + S.token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new Error('Could not reach the server.');
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* no JSON body */ }
  if (res.status === 401 && S.token && path !== '/admin/login') {
    logout();
    throw new Error('Your session has ended. Please log in again.');
  }
  if (!res.ok) throw new Error((data && data.error && data.error.message) || 'Request failed (' + res.status + ').');
  return data;
}

let toastTimer = null;
function toast(message, isError) {
  $toast.textContent = message;
  $toast.className = 'toast' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $toast.className = 'toast hidden'; }, isError ? 6000 : 3000);
}

/** Run an action from a button: disable the button meanwhile and show any error. */
async function act(button, fn) {
  if (button) button.disabled = true;
  try {
    await fn();
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

const taka = (n) => (n < 0 ? '−' : '') + '৳' + (Number.isInteger(n) ? Math.abs(n) : Math.abs(Number(n)).toFixed(2));
const when = (unix) => (unix ? new Date(unix * 1000).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : '—');
const badge = (status) => h('span', { class: 'badge ' + status }, String(status || '—').replace(/_/g, ' '));
const isSuper = () => S.me && S.me.admin_role === 'super_admin';
const hasRole = (...roles) => isSuper() || (S.me && roles.includes(S.me.admin_role));

function field(labelText, input) {
  return h('div', {}, h('label', {}, labelText), input);
}

function table(headers, rows) {
  if (!rows.length) return h('p', { class: 'muted' }, 'Nothing here.');
  return h('table', {},
    h('thead', {}, h('tr', {}, headers.map((x) => h('th', { class: x.num ? 'num' : null }, x.label)))),
    h('tbody', {}, rows));
}

// ----------------------------------------------------------------------------- shell

const VIEWS = [
  { id: 'dashboard', label: 'Dashboard', roles: null, render: dashboardView },
  { id: 'doctors', label: 'Doctor verification', roles: ['verifier', 'support_finance'], render: doctorsView },
  { id: 'users', label: 'Users and wallets', roles: ['support_finance'], render: usersView },
  { id: 'reports', label: 'Reports', roles: ['support_finance'], render: reportsView },
  { id: 'settings', label: 'Prices and settings', roles: [], render: settingsView },
  { id: 'admins', label: 'Admin accounts', roles: [], render: adminsView },
  { id: 'audit', label: 'Audit log', roles: [], render: auditView },
];
const allowed = (view) => view.roles === null || hasRole(...view.roles);

function logout() {
  S.token = null;
  S.me = null;
  sessionStorage.removeItem('admin_token');
  renderLogin();
}

function testStrip() {
  return S.config && S.config.test_mode
    ? h('div', { class: 'test-strip' }, 'TEST MODE is on: login codes are shown to anyone and top-ups are free. Turn it off (DEV_MODE=0) before real users.')
    : null;
}

function renderLogin() {
  const email = h('input', { type: 'email', autocomplete: 'username', required: true });
  const password = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const button = h('button', { class: 'btn', type: 'submit' }, 'Log in');
  const form = h('form', { class: 'login', onsubmit: (e) => {
    e.preventDefault();
    act(button, async () => {
      const result = await api('POST', '/admin/login', { email: email.value, password: password.value });
      S.token = result.token;
      sessionStorage.setItem('admin_token', S.token);
      await start();
    });
  } },
  h('h1', {}, '10 Takar Doctor · Admin'), field('Email', email), field('Password', password), h('div', { class: 'actions' }, button));
  $app.replaceChildren(...[testStrip(), form].filter(Boolean));
  email.focus();
}

let $main = null;
function renderShell() {
  $main = h('main', { class: 'main' });
  const nav = h('nav', { class: 'nav' },
    h('div', { class: 'brand' }, '10 Takar Doctor'),
    VIEWS.filter(allowed).map((view) => h('button', { 'data-view': view.id, onclick: () => show(view.id) }, view.label)),
    h('div', { class: 'who' },
      h('div', {}, S.me.email), h('div', { class: 'muted' }, S.me.admin_role.replace(/_/g, ' ')),
      h('div', { class: 'actions' }, h('button', { class: 'btn secondary', onclick: logout }, 'Log out'))));
  $app.replaceChildren(...[testStrip(), h('div', { class: 'shell' }, nav, $main)].filter(Boolean));
}

let showCounter = 0;
async function show(viewId, arg) {
  const view = VIEWS.find((v) => v.id === viewId);
  if (!view || !allowed(view)) return;
  S.view = viewId;
  for (const button of document.querySelectorAll('.nav button[data-view]')) {
    button.classList.toggle('active', button.getAttribute('data-view') === viewId);
  }
  if ($toast.classList.contains('error')) $toast.className = 'toast hidden';   // an old error should not follow the admin to a new screen
  const ticket = ++showCounter;   // if the admin clicks elsewhere while this loads, drop the stale result
  $main.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
  try {
    const content = await view.render(arg);
    if (ticket === showCounter) $main.replaceChildren(content);
  } catch (e) {
    if (ticket === showCounter) $main.replaceChildren(h('p', { class: 'warn' }, e.message));
  }
}

async function start() {
  try {
    S.config = await api('GET', '/config');
  } catch (e) { S.config = null; }
  if (!S.token) return renderLogin();
  try {
    S.me = await api('GET', '/admin/me');
  } catch (e) {
    return logout();
  }
  renderShell();
  show('dashboard');
}

// ----------------------------------------------------------------------------- dashboard

async function dashboardView() {
  const d = await api('GET', '/admin/dashboard');
  const card = (num, label) => h('div', { class: 'card' }, h('div', { class: 'num' }, num), h('div', { class: 'label' }, label));
  return h('div', {},
    h('h1', {}, 'Dashboard'),
    h('div', { class: 'cards' },
      card(d.doctors_pending, 'Doctors waiting for verification'),
      card(d.doctors_online, 'Doctors online now'),
      card(d.calls_in_progress, 'Calls in progress'),
      card(d.patients_waiting, 'Patients searching or waiting'),
      card(d.waiting_requests, 'Waiting requests'),
      card(d.open_reports, 'Open reports')),
    h('h2', {}, 'Today'),
    h('div', { class: 'cards' },
      card(d.today.calls, 'Completed calls'),
      card(taka(d.today.income_taka), 'Charged to patients'),
      card(taka(d.today.doctor_pay_taka), 'Owed to doctors'),
      h('div', { class: 'card' },
        h('div', { class: 'num ' + (d.today.margin_taka < 0 ? 'neg' : 'pos') }, taka(d.today.margin_taka)),
        h('div', { class: 'label' }, 'Left after doctor pay'))),
    h('div', { class: 'actions' }, h('button', { class: 'btn secondary', onclick: () => show('dashboard') }, 'Refresh')));
}

// ----------------------------------------------------------------------------- doctors

async function doctorsView(arg) {
  if (arg && arg.id) return doctorDetail(arg.id);
  const status = (arg && arg.status) || 'pending';
  const { doctors } = await api('GET', '/admin/doctors?status=' + encodeURIComponent(status));
  const tabs = ['pending', 'changes_requested', 'approved', 'rejected'].map((s) =>
    h('button', { class: s === status ? 'active' : null, onclick: () => show('doctors', { status: s }) }, s.replace(/_/g, ' ')));
  const rows = doctors.map((d) => h('tr', { class: 'clickable', onclick: () => show('doctors', { id: d.id }) },
    h('td', {}, d.real_name), h('td', {}, d.bmdc_number), h('td', {}, d.specialty),
    h('td', {}, DESIGNATIONS[d.designation] || '—'), h('td', {}, d.phone), h('td', {}, badge(d.status))));
  return h('div', {},
    h('h1', {}, 'Doctor verification'),
    h('div', { class: 'tabs' }, tabs),
    table([{ label: 'Name' }, { label: 'BMDC no.' }, { label: 'Specialty' }, { label: 'Designation' }, { label: 'Phone' }, { label: 'Status' }], rows));
}

async function loadDocument(doc, holder) {
  // Files are fetched with the admin's login and shown from memory; they never get a public address.
  const res = await fetch('/admin/documents/' + doc.id + '/file', { headers: { Authorization: 'Bearer ' + S.token } });
  if (!res.ok) throw new Error('Could not open the document (' + res.status + ').');
  const url = URL.createObjectURL(await res.blob());
  if (doc.content_type === 'application/pdf') {
    holder.replaceChildren(h('a', { href: url, download: doc.doc_type + '.pdf' }, 'Download PDF'));
  } else {
    holder.replaceChildren(h('img', { src: url, alt: DOC_NAMES[doc.doc_type] || doc.doc_type, onclick: () => {
      const overlay = h('div', { class: 'overlay', onclick: () => overlay.remove() }, h('img', { src: url, alt: '' }));
      document.body.append(overlay);
    } }));
  }
}

async function doctorDetail(id) {
  const d = await api('GET', '/admin/doctors/' + id);
  const canReview = hasRole('verifier');
  const info = h('dl', { class: 'kv' },
    h('dt', {}, 'Name'), h('dd', {}, d.real_name),
    h('dt', {}, 'BMDC registration no.'), h('dd', {}, d.bmdc_number),
    h('dt', {}, 'Phone'), h('dd', {}, d.phone),
    h('dt', {}, 'Gender'), h('dd', {}, d.gender),
    h('dt', {}, 'Specialty'), h('dd', {}, d.specialty),
    h('dt', {}, 'Experience'), h('dd', {}, d.experience_years + ' years'),
    h('dt', {}, 'Languages'), h('dd', {}, d.languages.join(', ')),
    h('dt', {}, 'Designation (pay tier)'), h('dd', {}, DESIGNATIONS[d.designation] || 'Not set'),
    h('dt', {}, 'Verification'), h('dd', {}, badge(d.status)),
    h('dt', {}, 'Account'), h('dd', {}, badge(d.account_status)),
    d.review_reason ? [h('dt', {}, 'Last message to doctor'), h('dd', {}, d.review_reason)] : null);

  const docs = h('div', { class: 'docs' }, d.documents.map((doc) => {
    const holder = h('div', { class: 'muted' }, canReview ? 'Loading…' : 'Only verifiers can open documents.');
    if (canReview) loadDocument(doc, holder).catch((e) => holder.replaceChildren(e.message));
    return h('div', { class: 'doc' }, holder, h('div', { class: 'name' }, DOC_NAMES[doc.doc_type] || doc.doc_type), h('div', { class: 'muted' }, when(doc.created_at)));
  }));

  const out = h('div', {},
    h('div', { class: 'actions' }, h('button', { class: 'btn secondary', onclick: () => show('doctors', { status: d.status === 'draft' ? 'pending' : d.status }) }, '← Back to list')),
    h('h1', {}, d.real_name),
    h('div', { class: 'panel' }, info),
    h('h2', {}, 'Documents'),
    d.documents.length ? docs : h('p', { class: 'muted' }, 'No documents uploaded.'));

  if (canReview && (d.status === 'pending' || d.status === 'approved')) {
    const designation = h('select', {}, h('option', { value: '' }, 'Choose…'),
      Object.entries(DESIGNATIONS).map(([key, label]) => h('option', { value: key, selected: d.designation === key }, label)));
    const specialty = h('select', {}, (S.config ? S.config.specialties : [d.specialty]).map((s) => h('option', { value: s, selected: s === d.specialty }, s)));
    const reason = h('textarea', { placeholder: 'Shown to the doctor. Needed to reject or ask for changes.' });
    const review = (action) => (e) => act(e.target, async () => {
      const body = { action, reason: reason.value.trim() };
      if (action === 'approve') { body.designation = designation.value || null; body.specialty = specialty.value; }
      if (action === 'reject' && !confirm('Reject this doctor?')) return;
      await api('POST', '/admin/doctors/' + id + '/review', body);
      toast('Saved.');
      show('doctors', { id });
    });
    out.append(
      h('h2', {}, d.status === 'approved' ? 'Change designation or specialty' : 'Decision'),
      h('div', { class: 'panel' },
        d.status === 'pending' ? h('div', { class: 'note' }, 'Before approving: check the BMDC number against the BMDC register, compare the selfie with the NID photo, and check the names match on every document.') : null,
        h('div', { class: 'row' },
          h('div', { class: 'grow' }, field('Designation (sets the pay tier)', designation)),
          h('div', { class: 'grow' }, field('Specialty', specialty))),
        d.status === 'pending' ? field('Message to the doctor', reason) : null,
        h('div', { class: 'actions' },
          h('button', { class: 'btn', onclick: review('approve') }, d.status === 'approved' ? 'Save' : 'Approve'),
          d.status === 'pending' ? h('button', { class: 'btn secondary', onclick: review('request_changes') }, 'Ask for changes') : null,
          d.status === 'pending' ? h('button', { class: 'btn danger', onclick: review('reject') }, 'Reject') : null)));
  }
  return out;
}

// ----------------------------------------------------------------------------- users and wallets

async function usersView(arg) {
  if (arg && arg.id) return userDetail(arg.id);
  const q = (arg && arg.q) || '';
  const { users } = await api('GET', '/admin/users?q=' + encodeURIComponent(q));
  const search = h('input', { type: 'search', placeholder: 'Phone number, name or nickname', value: q });
  const form = h('form', { class: 'row', onsubmit: (e) => { e.preventDefault(); show('users', { q: search.value.trim() }); } },
    h('div', { class: 'grow' }, search), h('button', { class: 'btn', type: 'submit' }, 'Search'));
  const rows = users.map((u) => h('tr', { class: 'clickable', onclick: () => show('users', { id: u.id }) },
    h('td', {}, u.name || u.alias), h('td', {}, u.role), h('td', {}, u.phone),
    h('td', { class: 'num' }, taka(u.balance_taka)), h('td', {}, badge(u.status)), h('td', {}, when(u.created_at))));
  return h('div', {},
    h('h1', {}, 'Users and wallets'), form, h('h2', {}, q ? 'Results' : 'Newest accounts'),
    table([{ label: 'Name' }, { label: 'Role' }, { label: 'Phone' }, { label: 'Balance', num: true }, { label: 'Status' }, { label: 'Joined' }], rows));
}

async function userDetail(id) {
  const u = await api('GET', '/admin/users/' + id);
  const reload = () => show('users', { id });
  const isDoctor = u.role === 'doctor';

  const suspendReason = h('input', { placeholder: 'Reason (kept in the audit log)' });
  const suspended = u.status === 'suspended';
  const suspendButton = h('button', { class: 'btn ' + (suspended ? 'secondary' : 'danger'), onclick: (e) => act(e.target, async () => {
    if (suspendReason.value.trim().length < 2) throw new Error('Give a reason.');
    await api('POST', '/admin/users/' + id + '/suspend', { suspended: !suspended, reason: suspendReason.value.trim() });
    toast(suspended ? 'Account restored.' : 'Account suspended.');
    reload();
  }) }, suspended ? 'Restore account' : 'Suspend account');

  const amount = h('input', { class: 'narrow', type: 'number', step: '0.01', min: '0.01', placeholder: 'Taka' });
  const kind = h('select', {},
    h('option', { value: 'add' }, 'Add to balance (refund or correction)'),
    h('option', { value: 'remove' }, 'Remove from balance (correction)'),
    isDoctor ? h('option', { value: 'payout' }, 'Record a payout already sent to the doctor') : null);
  const moneyReason = h('input', { placeholder: 'Reason (kept in the audit log)' });
  const moneyButton = h('button', { class: 'btn', onclick: (e) => act(e.target, async () => {
    const value = Number(amount.value);
    if (!(value > 0)) throw new Error('Enter an amount above zero.');
    if (moneyReason.value.trim().length < 2) throw new Error('Give a reason.');
    const signed = kind.value === 'add' ? value : -value;
    if (!confirm((signed > 0 ? 'Add ' : 'Remove ') + taka(value) + (signed > 0 ? ' to ' : ' from ') + (u.name || u.alias) + "'s balance?")) return;
    await api('POST', '/admin/wallet/adjust', { user_id: id, amount_taka: signed, reason: moneyReason.value.trim(), kind: kind.value === 'payout' ? 'payout' : 'adjust' });
    toast('Balance updated.');
    reload();
  }) }, 'Apply');

  const rows = u.transactions.map((x) => h('tr', {},
    h('td', {}, when(x.created_at)), h('td', {}, x.type), h('td', {}, x.note || ''),
    h('td', { class: 'num ' + (x.amount_taka < 0 ? 'neg' : 'pos') }, taka(x.amount_taka)), h('td', { class: 'num' }, taka(x.balance_after_taka))));

  return h('div', {},
    h('div', { class: 'actions' }, h('button', { class: 'btn secondary', onclick: () => show('users') }, '← Back to list')),
    h('h1', {}, u.name || u.alias),
    h('div', { class: 'panel' }, h('dl', { class: 'kv' },
      h('dt', {}, 'Role'), h('dd', {}, u.role + (isDoctor && u.designation ? ' · ' + (DESIGNATIONS[u.designation] || u.designation) : '')),
      h('dt', {}, 'Phone'), h('dd', {}, u.phone),
      h('dt', {}, 'Nickname shown in calls'), h('dd', {}, u.alias),
      h('dt', {}, 'Account'), h('dd', {}, badge(u.status)),
      isDoctor ? [h('dt', {}, 'Verification'), h('dd', {}, badge(u.doctor_status || 'draft'))] : null,
      h('dt', {}, 'Balance'), h('dd', {}, taka(u.balance_taka)),
      h('dt', {}, 'Completed calls'), h('dd', {}, u.completed_calls),
      h('dt', {}, 'Reports against them'), h('dd', {}, u.reports_against),
      h('dt', {}, 'Joined'), h('dd', {}, when(u.created_at)))),
    h('h2', {}, 'Wallet'),
    h('div', { class: 'panel' }, h('div', { class: 'row' }, field('Amount', amount), h('div', { class: 'grow' }, field('What to do', kind)), h('div', { class: 'grow' }, field('Reason', moneyReason)), moneyButton)),
    h('h2', {}, suspended ? 'Restore' : 'Suspend'),
    h('div', { class: 'panel' }, h('div', { class: 'row' }, h('div', { class: 'grow' }, field('Reason', suspendReason)), suspendButton)),
    h('h2', {}, 'Last 30 wallet entries'),
    table([{ label: 'When' }, { label: 'Type' }, { label: 'Note' }, { label: 'Amount', num: true }, { label: 'Balance after', num: true }], rows));
}

// ----------------------------------------------------------------------------- reports

async function reportsView(arg) {
  const status = (arg && arg.status) || 'open';
  const { reports } = await api('GET', '/admin/reports?status=' + status);
  const person = (p) => (p ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); show('users', { id: p.id }); } }, (p.name || p.alias) + ' (' + p.role + ')') : '—');
  const items = reports.map((r) => {
    const resolution = h('input', { placeholder: 'What was done about it' });
    return h('div', { class: 'panel' },
      h('dl', { class: 'kv' },
        h('dt', {}, 'Reason'), h('dd', {}, h('strong', {}, r.reason.replace(/_/g, ' '))),
        h('dt', {}, 'Details'), h('dd', {}, r.details || '—'),
        h('dt', {}, 'Reported person'), h('dd', {}, person(r.target)),
        h('dt', {}, 'Reported by'), h('dd', {}, person(r.reporter)),
        h('dt', {}, 'Call'), h('dd', {}, r.consultation ? when(r.consultation.started_at) + ' · ' + (r.consultation.duration_seconds || 0) + ' s · “' + r.consultation.problem + '”' : '—'),
        h('dt', {}, 'Received'), h('dd', {}, when(r.created_at)),
        r.status === 'resolved' ? [h('dt', {}, 'Resolution'), h('dd', {}, r.resolution)] : null),
      r.status === 'open' ? h('div', { class: 'row' }, h('div', { class: 'grow' }, field('Resolution', resolution)),
        h('button', { class: 'btn', onclick: (e) => act(e.target, async () => {
          if (resolution.value.trim().length < 2) throw new Error('Say what was done.');
          await api('POST', '/admin/reports/' + r.id + '/resolve', { resolution: resolution.value.trim() });
          toast('Report resolved.');
          show('reports', { status });
        }) }, 'Mark resolved')) : null);
  });
  return h('div', {},
    h('h1', {}, 'Reports'),
    h('div', { class: 'tabs' }, ['open', 'resolved'].map((s) => h('button', { class: s === status ? 'active' : null, onclick: () => show('reports', { status: s }) }, s))),
    h('p', { class: 'muted' }, 'To suspend someone, open their name and use Suspend on their page.'),
    items.length ? items : h('p', { class: 'muted' }, 'Nothing here.'));
}

// ----------------------------------------------------------------------------- settings

const NUMBER_SETTINGS = [
  ['instant_call_price_taka', 'Instant call price', 'taka per connected call'],
  ['instant_call_max_minutes', 'Instant call length', 'minutes; the call is cut off at this length'],
  ['free_skip_seconds', 'Free skip window', 'seconds; a call ended within this time costs nothing'],
  ['free_skips_per_day', 'Free skips per patient per day', 'after this, quick skips are charged'],
  ['offer_timeout_seconds', 'Time for a doctor to answer', 'seconds before the call moves to the next doctor'],
  ['waiting_request_minutes', 'Waiting request lifetime', 'minutes a saved request waits for a matching doctor'],
  ['patient_join_seconds', 'Time for a patient to join', 'seconds, once a doctor picks up their waiting request'],
  ['min_topup_taka', 'Smallest top-up', 'taka'],
  ['doctor_registration_fee_taka', 'Doctor registration fee', 'taka, taken once when a doctor submits; 0 for none'],
];

async function settingsView() {
  const saved = await api('GET', '/admin/settings');
  const inputs = {};
  const number = (value) => h('input', { class: 'narrow', type: 'number', step: 'any', min: '0', value: String(value) });

  const simpleRows = NUMBER_SETTINGS.map(([key, label, help]) => {
    inputs[key] = number(saved[key]);
    return h('tr', {}, h('td', {}, label), h('td', {}, inputs[key]), h('td', { class: 'muted' }, help));
  });

  // Doctor pay: per call + per minute by designation, with the cost of a full-length call worked out live.
  const pay = {};
  const booking = {};
  const fullCells = {};
  const marginCells = {};
  const recalc = () => {
    const price = Number(inputs.instant_call_price_taka.value);
    const minutes = Number(inputs.instant_call_max_minutes.value);
    for (const key of Object.keys(DESIGNATIONS)) {
      const full = Number(pay[key].call.value) + Number(pay[key].minute.value) * minutes;
      fullCells[key].textContent = taka(Math.round(full * 100) / 100);
      const margin = Math.round((price - full) * 100) / 100;
      marginCells[key].textContent = taka(margin);
      marginCells[key].className = 'num ' + (margin < 0 ? 'neg' : 'pos');
    }
  };
  const payRows = Object.entries(DESIGNATIONS).map(([key, label]) => {
    pay[key] = { call: number(saved.doctor_pay[key].per_call_taka), minute: number(saved.doctor_pay[key].per_minute_taka) };
    booking[key] = number(saved.booking_price_taka[key]);
    fullCells[key] = h('td', { class: 'num' });
    marginCells[key] = h('td', { class: 'num' });
    for (const input of [pay[key].call, pay[key].minute]) input.addEventListener('input', recalc);
    return h('tr', {}, h('td', {}, label), h('td', {}, pay[key].call), h('td', {}, pay[key].minute), fullCells[key], marginCells[key], h('td', {}, booking[key]));
  });
  inputs.instant_call_price_taka.addEventListener('input', recalc);
  inputs.instant_call_max_minutes.addEventListener('input', recalc);

  const specialties = h('input', { value: saved.specialties.join(', ') });
  const terms = h('input', { class: 'narrow', value: saved.terms_version });

  const num = (input) => (input.value.trim() === '' ? NaN : Number(input.value));   // an empty box is an error, not zero
  const collect = () => {
    const next = {};
    for (const [key] of NUMBER_SETTINGS) next[key] = num(inputs[key]);
    next.doctor_pay = {};
    next.booking_price_taka = {};
    for (const key of Object.keys(DESIGNATIONS)) {
      next.doctor_pay[key] = { per_call_taka: num(pay[key].call), per_minute_taka: num(pay[key].minute) };
      next.booking_price_taka[key] = num(booking[key]);
    }
    next.specialties = specialties.value.split(',').map((s) => s.trim().toLowerCase().replace(/\s+/g, '_')).filter(Boolean);
    next.terms_version = terms.value.trim();
    const changes = {};
    for (const key of Object.keys(next)) {
      if (JSON.stringify(next[key]) !== JSON.stringify(saved[key])) changes[key] = next[key];
    }
    return changes;
  };

  const save = h('button', { class: 'btn', onclick: (e) => act(e.target, async () => {
    const changes = collect();
    const keys = Object.keys(changes);
    if (!keys.length) return toast('Nothing was changed.');
    for (const key of keys) {
      const flat = JSON.stringify(changes[key]);
      if (flat.includes('null') || flat.includes('NaN')) throw new Error('Every number must be filled in.');
    }
    if (!confirm('Change ' + keys.length + ' setting(s)?\n\n' + keys.join('\n') + '\n\nNew values apply to new calls only.')) return;
    await api('PUT', '/admin/settings', changes);
    S.config = await api('GET', '/config');
    toast('Settings saved.');
    show('settings');
  }) }, 'Save changes');

  const out = h('div', {},
    h('h1', {}, 'Prices and settings'),
    h('p', { class: 'muted' }, 'Changes apply to new calls only, and each change is written to the audit log.'),
    h('h2', {}, 'Calls'),
    table([{ label: 'Setting' }, { label: 'Value' }, { label: 'Meaning' }], simpleRows),
    h('h2', {}, 'Doctor pay and booking price, by designation'),
    table([{ label: 'Designation' }, { label: 'Per call (taka)' }, { label: 'Per minute (taka)' }, { label: 'Pay for a full-length call', num: true },
      { label: 'Left from the call price', num: true }, { label: 'Booking price (taka)' }], payRows),
    h('p', { class: 'muted' }, 'A red figure means a full-length call pays the doctor more than the patient is charged. Booking prices are saved now; bookings themselves are not built yet.'),
    h('h2', {}, 'Other'),
    h('div', { class: 'panel' },
      field('Specialties patients can filter by (comma-separated)', specialties),
      h('p', { class: 'muted' }, 'Removing a specialty that doctors already use hides those doctors from that filter.'),
      field('Terms version', terms),
      h('p', { class: 'muted' }, 'Changing this makes every patient and doctor accept the terms again before their next call.')),
    h('div', { class: 'actions' }, save));
  recalc();
  return out;
}

// ----------------------------------------------------------------------------- admins and audit

async function adminsView() {
  const { admins } = await api('GET', '/admin/admins');
  const email = h('input', { type: 'email' });
  const password = h('input', { type: 'password', autocomplete: 'new-password', placeholder: 'At least 10 characters' });
  const role = h('select', {},
    h('option', { value: 'verifier' }, 'Verifier: checks doctors'),
    h('option', { value: 'support_finance' }, 'Support and finance: users, money, reports'),
    h('option', { value: 'super_admin' }, 'Super admin: everything, including prices'));
  const create = h('button', { class: 'btn', onclick: (e) => act(e.target, async () => {
    await api('POST', '/admin/admins', { email: email.value.trim(), password: password.value, admin_role: role.value });
    toast('Admin created.');
    show('admins');
  }) }, 'Create admin');
  const rows = admins.map((a) => h('tr', {}, h('td', {}, a.email), h('td', {}, a.admin_role.replace(/_/g, ' ')), h('td', {}, badge(a.status)), h('td', {}, when(a.created_at))));
  return h('div', {},
    h('h1', {}, 'Admin accounts'),
    table([{ label: 'Email' }, { label: 'Role' }, { label: 'Status' }, { label: 'Created' }], rows),
    h('h2', {}, 'Add an admin'),
    h('div', { class: 'panel' }, h('div', { class: 'row' },
      h('div', { class: 'grow' }, field('Email', email)), h('div', { class: 'grow' }, field('Password', password)), h('div', { class: 'grow' }, field('Role', role)), create)));
}

async function auditView() {
  const [{ entries }, { admins }] = await Promise.all([api('GET', '/admin/audit'), api('GET', '/admin/admins')]);
  const emails = Object.fromEntries(admins.map((a) => [a.id, a.email]));
  const rows = entries.map((x) => h('tr', {},
    h('td', {}, when(x.created_at)), h('td', {}, emails[x.admin_id] || x.admin_id), h('td', {}, x.action.replace(/_/g, ' ')),
    h('td', {}, x.target || ''), h('td', {}, Object.keys(x.detail).length ? JSON.stringify(x.detail) : '')));
  return h('div', {},
    h('h1', {}, 'Audit log'),
    h('p', { class: 'muted' }, 'The 200 most recent admin actions. Entries cannot be edited or removed from this panel.'),
    table([{ label: 'When' }, { label: 'Admin' }, { label: 'Action' }, { label: 'Target' }, { label: 'Detail' }], rows));
}

start();
