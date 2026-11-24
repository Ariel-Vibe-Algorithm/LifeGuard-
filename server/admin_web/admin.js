/* LifeGuard admin page. No third-party code, no innerHTML with data (everything via textContent).
   The admin token lives in sessionStorage only (gone when the tab closes); the server keeps only its hash. */
'use strict';

(function () {
  const KEY = 'lg_admin_session';
  const $ = (id) => document.getElementById(id);
  const NF = new Intl.NumberFormat('he-IL');
  let data = null;
  let expiryTimer = null;

  // ---------------------------------------------------------------- Hebrew labels for codes
  const L = {
    loud: 'גלוי', silent: 'שקט', active: 'פעיל', resolved: 'נפתר', cancelled: 'בוטל',
    manual: 'ידני', confirmed_high: 'זיהוי - ביטחון גבוה', unconfirmed: 'זיהוי - לא מאושר',
    confirmed_by_user: 'זיהוי - אושר ע"י המשתמש', server_timer: 'טיימר שרת', support: 'בקשת תמיכה (לא חירום)',
    auto: 'אוטומטי', sensor: 'חיישן', wellness_no_response: 'בדיקת שלום - אין מענה', acoustic: 'אקוסטי',
    watch_fall: 'נפילה (שעון)', wearable: 'לביש',
    on_scene: 'הגעה למקום', remote_support: 'תמיכה מרחוק', authority_liaison: 'הזעקת רשויות',
    unverified: 'לא מאומת', pending: 'ממתין', verified: 'מאומת', rejected: 'נדחה', corroborated: 'מאושש',
    accepted: 'התקבל', declined: 'נדחה', expired: 'פג', notified: 'הוזעק', delivered: 'נמסר', queued: 'בתור',
    revoked: 'בוטלה גישה', reassigned: 'הועבר', done: 'הסתיים', offered_no_accept: 'הוצע, אף אחד לא קיבל',
    no_helper_available: 'אין מתנדב/ת זמין/ה',
    always: 'תמיד', emergency: 'רק בחירום', off: 'כבוי',
    walk: 'ליווי הליכה', ride: 'נסיעה', arrived: 'הגיע/ה', escalated: 'הוסלם לאירוע',
    HIGH: 'גבוה', MEDIUM: 'בינוני', LOW: 'נמוך', UNKNOWN: 'לא ידוע', unknown: 'לא ידוע',
    medical: 'רפואי', fire: 'שריפה', earthquake: 'רעידת אדמה', security: 'ביטחוני', physical_danger: 'סכנה פיזית',
    road_accident: 'תאונת דרכים', lost: 'אבוד/ה', missing_person: 'נעדר/ת', flood_weather: 'הצפה / מזג אוויר',
    power_outage: 'הפסקת חשמל', false_alarm: 'התראת שווא', abusive: 'ניצול לרעה',
    dark_street: 'רחוב חשוך', broken_light: 'פנס תקול', harassment: 'הטרדה', unsafe_crossing: 'מעבר מסוכן',
    isolated_area: 'אזור מבודד', safe_place: 'מקום בטוח', sent: 'נשלח', failed: 'נכשל',
    configured: 'מוגדר', not_configured: 'לא מוגדר', reachable: 'זמין', unreachable: 'לא זמין', misconfigured: 'הגדרה שגויה',
    taxi: 'מונית', rideshare: 'נסיעה שיתופית', bus: 'אוטובוס', train: 'רכבת', private: 'רכב פרטי',
    verified_official: 'רשמי מאומת', open_data_community: 'פתוח / קהילתי', estimate: 'הערכה', user_report: 'דיווח משתמשים',
    news_unverified: 'חדשות לא מאומתות', '—': 'ללא', ok: 'תקין', degraded: 'פגום',
  };
  const lab = (k) => (k in L ? L[k] : String(k).split(':').map((p) => (p in L ? L[p] : p)).join(' · '));

  // ---------------------------------------------------------------- DOM helpers
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) {
      if (c === null || c === undefined || c === false) continue;
      n.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
    }
    return n;
  }
  const num = (v) => (v === null || v === undefined ? '—' : typeof v === 'number' ? NF.format(v) : String(v));
  const pct = (r) => (r === null || r === undefined ? '—' : `${(r * 100).toFixed(1)}%`);
  function dur(s) {
    if (s === null || s === undefined) return '—';
    s = Math.round(s);
    if (s < 60) return `${s} שנ'`;
    if (s < 3600) return `${Math.floor(s / 60)} דק' ${s % 60 ? `${s % 60} שנ'` : ''}`.trim();
    if (s < 86400) return `${Math.floor(s / 3600)} שע' ${Math.round((s % 3600) / 60)} דק'`;
    return `${Math.floor(s / 86400)} ימים ${Math.round((s % 86400) / 3600)} שע'`;
  }
  const when = (t) => (t ? new Date(t * 1000).toLocaleString('he-IL') : '—');
  function bytes(b) {
    if (b === null || b === undefined) return '—';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i += 1; }
    return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
  }
  const isNA = (v) => v && typeof v === 'object' && 'reason' in v && v.value === null;
  const naSpan = (v) => el('span', { class: 'na', text: `לא זמין: ${v.reason}` });
  function badge(state) {
    const good = ['ok', 'configured', 'reachable', 'active', 'נטען', true];
    const bad = ['unreachable', 'misconfigured', 'degraded'];          // false = סתם 'לא' (ניטרלי), לא תקלה
    const cls = good.includes(state) ? 'ok' : bad.includes(state) ? 'bad' : state === 'not_configured' ? 'warn' : 'neutral';
    const txt = state === true ? 'כן' : state === false ? 'לא' : lab(state);
    const icon = cls === 'ok' ? '✓ ' : cls === 'bad' ? '✕ ' : cls === 'warn' ? '! ' : '';
    return el('span', { class: `badge ${cls}`, text: icon + txt });
  }

  function card(title, ...kids) { return el('section', { class: 'card' }, el('h2', { text: title }), ...kids); }
  function rows(pairs) {
    return el('ul', { class: 'rows' }, pairs.filter(Boolean).map(([k, v]) => el('li', {},
      el('span', { text: k }),
      el('span', { class: 'r-val' }, isNA(v) ? naSpan(v) : v instanceof Node ? v : num(v)))));
  }
  function bars(obj, opts) {
    const entries = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
    if (!entries.length) return el('p', { class: 'empty', text: (opts && opts.empty) || 'אין נתונים עדיין' });
    const max = Math.max(...entries.map((e) => e[1]), 1);
    const total = entries.reduce((s, e) => s + e[1], 0);
    return el('div', { class: 'bars' }, entries.map(([k, v]) => {
      const fill = el('div', { class: 'bar-fill' });
      fill.style.width = `${Math.max(1, (v / max) * 100)}%`;        // CSSOM - מותר תחת CSP
      return el('div', { class: 'bar-row', title: `${lab(k)}: ${num(v)} (${((v / total) * 100).toFixed(1)}%)` },
        el('span', { class: 'bar-label', text: lab(k) }),
        el('div', { class: 'bar-track', 'aria-hidden': 'true' }, fill),
        el('span', { class: 'bar-val', text: num(v) }));
    }));
  }

  // ---------------------------------------------------------------- session + API
  function session() {
    try {
      const s = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      return s && s.token && s.expires_at * 1000 > Date.now() ? s : null;
    } catch (e) { return null; }
  }
  function saveSession(s) { try { sessionStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* private mode */ } }
  function clearSession() { try { sessionStorage.removeItem(KEY); } catch (e) { /* ignore */ } }

  async function api(path, opts) {
    const s = session();
    const headers = { 'Content-Type': 'application/json' };
    if (s) headers.Authorization = `Bearer ${s.token}`;
    const res = await fetch(`/v1${path}`, { method: 'GET', cache: 'no-store', ...opts, headers });
    let body = {};
    try { body = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) { const err = new Error(body.message || `שגיאה ${res.status}`); err.status = res.status; err.body = body; throw err; }
    return body;
  }

  function showLogin(msg) {
    clearTimeout(expiryTimer);
    data = null;
    $('dashView').hidden = true;
    $('barActions').hidden = true;
    $('loading').hidden = true;
    $('loginView').hidden = false;
    const e = $('loginError');
    e.hidden = !msg;
    e.textContent = msg || '';
    $('loginPass').value = '';
    $('loginUser').focus();
  }

  async function onLogin(ev) {
    ev.preventDefault();
    const btn = $('loginBtn');
    btn.disabled = true;
    $('loginError').hidden = true;
    try {
      const r = await api('/admin/login', {
        method: 'POST',
        body: JSON.stringify({ username: $('loginUser').value, password: $('loginPass').value }),
      });
      $('loginPass').value = '';
      saveSession({ token: r.token, expires_at: r.expires_at, username: r.username });
      await loadDashboard();
    } catch (e) {
      showLogin(e.message || 'הכניסה נכשלה');
    } finally {
      btn.disabled = false;
    }
  }

  async function onLogout() {
    try { await api('/admin/logout', { method: 'POST', body: '{}' }); } catch (e) { /* already gone */ }
    clearSession();
    showLogin('התנתקת.');
  }

  async function loadDashboard() {
    const s = session();
    if (!s) { showLogin(); return; }
    $('loginView').hidden = true;
    $('loading').hidden = false;
    try {
      data = await api('/admin/dashboard');
    } catch (e) {
      if (e.status === 401 || e.status === 404) { clearSession(); showLogin('ההתחברות פגה - יש להתחבר מחדש.'); return; }
      $('loading').textContent = e.message || 'שגיאה בטעינה';
      return;
    }
    $('loading').hidden = true;
    $('dashView').hidden = false;
    $('barActions').hidden = false;
    $('privacyNote').textContent = data.privacy_note;
    $('metaLine').textContent = `מחובר/ת כ-${s.username} · עודכן ${when(data.generated_at)} · חושב ב-${data.compute_ms} מ"ש · ` +
      `ההתחברות פגה ב-${when(s.expires_at)}`;
    clearTimeout(expiryTimer);
    expiryTimer = setTimeout(() => { clearSession(); showLogin('ההתחברות פגה - יש להתחבר מחדש.'); },
      Math.max(0, s.expires_at * 1000 - Date.now()));
    renderStats(); renderHealth(); renderData(); renderEngine(); renderImprovements();
  }

  function selectTab(name) {
    document.querySelectorAll('.segmented [role=tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    document.querySelectorAll('.tab').forEach((t) => { t.hidden = t.id !== `tab-${name}`; });
  }

  // ---------------------------------------------------------------- סטטיסטיקות
  function kpi(label, value, sub) {
    return el('div', { class: 'kpi' }, el('div', { class: 'k-label', text: label }),
      el('div', { class: 'k-value', text: value }), sub ? el('div', { class: 'k-sub', text: sub }) : null);
  }

  function renderStats() {
    const u = data.usage;
    const box = $('tab-stats');
    box.replaceChildren();
    const fa = u.response.first_acceptance_any;
    box.append(el('div', { class: 'kpis' },
      kpi('משתמשים', num(u.users.total), `+${num(u.users.new_7d)} ב-7 ימים · +${num(u.users.new_30d)} ב-30`),
      kpi('אירועים פעילים עכשיו', num(u.incidents.active_now),
        `${num(u.incidents.active_now_waiting_for_volunteer)} ממתינים למתנדב`),
      kpi('אירועים (סה"כ)', num(u.incidents.all_time.total), `${num(u.incidents.windows['24h'].total)} ב-24 שעות`),
      kpi('מתנדבים זמינים', num(u.volunteers.available_now), `מתוך ${num(u.volunteers.registered)} רשומים`),
      kpi('מסירת התראות', pct(u.delivery.recipients_all.rate),
        `${num(u.delivery.recipients_all.delivered)} / ${num(u.delivery.recipients_all.sent)} נמענים`),
      kpi('זמן חציוני לקבלה ראשונה', isNA(fa) ? '—' : dur(fa.median_s), isNA(fa) ? fa.reason : `p90 ${dur(fa.p90_s)} · n=${fa.n}`)));

    // אירועים לפי חלון זמן
    const windows = { all_time: 'מאז ומעולם', '30d': '30 ימים', '7d': '7 ימים', '24h': '24 שעות' };
    let current = 'all_time';
    const incBody = el('div');
    const pills = el('div', { class: 'pills', role: 'group', 'aria-label': 'חלון זמן' });
    const drawInc = () => {
      const b = current === 'all_time' ? u.incidents.all_time : u.incidents.windows[current];
      pills.querySelectorAll('button').forEach((p) => p.setAttribute('aria-pressed', String(p.dataset.w === current)));
      incBody.replaceChildren(
        rows([['סה"כ בחלון', b.total]]),
        el('h3', { text: 'מצב' }), bars(b.by_status),
        el('h3', { text: 'גלוי / שקט' }), bars(b.by_mode),
        el('h3', { text: 'אופן הפעלה (activation)' }), bars(b.by_activation),
        el('h3', { text: 'טריגר (trigger_kind)' }), bars(b.by_trigger_kind),
        el('h3', { text: 'סוג חירום' }), bars(b.by_emergency_type),
        el('h3', { text: 'רמת סכנה (מהמכשיר)' }), bars(b.by_risk_level));
    };
    Object.entries(windows).forEach(([k, v]) => pills.append(el('button', {
      type: 'button', 'data-w': k, text: v, onclick: () => { current = k; drawInc(); },
    })));
    drawInc();

    const resp = u.response;
    const respRow = (name, s) => (isNA(s) ? [name, s] : [name, el('span', {}, `חציון ${dur(s.median_s)} · p90 ${dur(s.p90_s)} · n=${s.n}`)]);
    const d = u.delivery;
    const v = u.volunteers;
    const helperRows = Object.entries(u.helpers.requests_by_kind || {}).map(([k, x]) =>
      [lab(k), el('span', {}, `${num(x.total)} בקשות · ${num(x.accepted)} התקבלו · ${num(x.offered_no_accept)} בלי מענה · ` +
        `${num(x.no_helper_available)} בלי מתנדב/ת`)]);
    const nat = u.national_mode;
    const ec = u.escorts;
    const pv = u.privacy_features;

    box.append(el('div', { class: 'grid' },
      card('אירועי חירום', pills, incBody,
        el('h3', { text: 'משך עד סיום' }),
        rows([isNA(u.incidents.duration_s) ? ['משך', u.incidents.duration_s]
          : ['חציון / p90', el('span', {}, `${dur(u.incidents.duration_s.median)} / ${dur(u.incidents.duration_s.p90)} (n=${u.incidents.duration_s.n})`)]]),
        el('h3', { text: 'דיווחי מתנדבים על אירועים' }), bars(u.incidents.false_alarm_reports)),
      el('div', {},
        card('זמן תגובה', rows([
          ['אירועים שנבדקו', resp.incidents_considered], ['אירועים שמישהו קיבל', resp.incidents_with_acceptance],
          respRow('קבלה ראשונה - כל מגיב', resp.first_acceptance_any),
          respRow('קבלה ראשונה - איש קשר', resp.first_acceptance_trusted),
          respRow('קבלה ראשונה - מתנדב/ת', resp.first_acceptance_volunteer)]),
        el('p', { class: 'muted small', text: `שיטה: ${resp.method}` })),
        card('מסירת התראות', rows([
          ['כל הנמענים', el('span', {}, `${pct(d.recipients_all.rate)} (${num(d.recipients_all.delivered)}/${num(d.recipients_all.sent)})`)],
          ['אנשי קשר', el('span', {}, `${pct(d.recipients_trusted.rate)} (${num(d.recipients_trusted.delivered)}/${num(d.recipients_trusted.sent)})`)],
          ['מתנדבים', el('span', {}, `${pct(d.recipients_volunteer.rate)} (${num(d.recipients_volunteer.delivered)}/${num(d.recipients_volunteer.sent)})`)],
          ['30 ימים אחרונים', el('span', {}, `${pct(d.recipients_30d.rate)} (${num(d.recipients_30d.delivered)}/${num(d.recipients_30d.sent)})`)],
          ['כל ההתראות (כולל עדכונים)', el('span', {}, `${pct(d.notifications.rate)} (${num(d.notifications.received_by_app)}/${num(d.notifications.created)})`)]]),
        el('h3', { text: 'מצבי נמענים' }), bars(d.recipient_states),
        el('h3', { text: 'SMS' }), bars(d.sms, { empty: 'לא נשלחו SMS' }),
        el('p', { class: 'muted small', text: d.definition }))),
      card('מתנדבים', rows([['רשומים', v.registered], ['זמינים עכשיו (לא מושעים)', v.available_now], ['מושעים', v.suspended],
        ['מסומנים לבדיקה', v.review_flag],
        ['דירוג ממוצע', v.feedback && v.feedback.ratings ? el('span', {}, `${v.feedback.avg_rating} (${num(v.feedback.ratings)} דירוגים)`) : '—'],
        ['דיווחים על מתנדבים', v.feedback ? v.feedback.reports : 0]]),
      el('h3', { text: 'לפי תפקיד' }), bars(v.by_role),
      el('h3', { text: 'לפי סטטוס אימות' }), bars(v.by_verification),
      el('h3', { text: 'הכשרות (הכשרה · סטטוס)' }), bars(v.qualifications)),
      card('תמיכה מרחוק והזעקת רשויות', helperRows.length ? rows(helperRows) : el('p', { class: 'empty', text: 'אין בקשות עדיין' }),
        rows([['בקשות "צריך/ה לדבר" (בלי חירום)', u.helpers.support_requests_no_emergency]]),
        el('h3', { text: 'פניות למתנדבים (סוג · מצב)' }), bars(u.helpers.helper_offer_states)),
      card('דיווחי רחוב', rows([['סה"כ', u.street_reports.total], ['בתוקף עכשיו', u.street_reports.active],
        ['תצפיות מהליכות מתועדות', u.street_reports.walk_observations]]),
      el('h3', { text: 'לפי סוג' }), bars(u.street_reports.by_kind),
      el('h3', { text: 'לפי סטטוס' }), bars(u.street_reports.by_status)),
      card('מעגל הביטחון', rows([['קשרים מאושרים', u.circle.relationships], ['עם הרשאת הקלטות', u.circle.with_recording_access]]),
        el('h3', { text: 'מצב מיקום לקשר' }), bars(u.circle.by_location_mode),
        el('h3', { text: 'הזמנות' }), bars(u.circle.invitations),
        el('h3', { text: 'קישורי הורה-ילד' }), bars(u.circle.guardian_links)),
      card('ליווי ונסיעות', rows([['פעילים עכשיו', ec.active_now], ['פעילים שעבר זמנם', ec.active_past_deadline],
        ['הוסלמו לאירוע (סה"כ)', ec.escalated_total]]),
      el('h3', { text: 'סוג · מצב' }), bars(ec.by_kind_status),
      el('h3', { text: 'נסיעות לפי סוג' }), bars(ec.rides_by_type)),
      card('משתמשים ופרטיות', rows([['כניסות פעילות (טוקנים)', u.users.active_sessions], ['טלפון מאומת', u.users.phone_verified],
        ['הורה/אפוטרופוס מאומת', u.users.guardian_verified], ['פרופילים רפואיים', pv.medical_profiles],
        ['משתפים מיקום חי', pv.live_location_sharers], ['השהו שיתוף', pv.sharing_paused],
        ['הקלטות שמורות', pv.recordings_stored], ['הקלטות שנמחקו', pv.recordings_deleted],
        ['מכשירי Web Push', pv.web_push_devices], ['מכשירי FCM (אנדרואיד)', pv.fcm_devices]])),
      card('מצב חירום לאומי', rows([['מצב', badge(nat.active ? 'active' : 'off')],
        nat.active ? ['כותרת', nat.title] : null, nat.active ? ['מאז', when(nat.since)] : null,
        nat.active ? ['אזורים', nat.areas_count] : null]))));
  }

  // ---------------------------------------------------------------- תקינות
  function renderHealth() {
    const h = data.health;
    const s = h.server;
    const db = h.database;
    const box = $('tab-health');
    box.replaceChildren(el('div', { class: 'grid' },
      card('שרת', rows([['גרסת API', s.api_version], ['כותרת שרת', s.server_header],
        ['קומיט', isNA(s.git) ? s.git : el('span', { class: 'mono', text: `${s.git.commit}${s.git.branch ? ` (${s.git.branch})` : ''}` })],
        ['Python', s.python], ['פועל מאז', when(s.started_at)], ['זמן פעילות', dur(s.uptime_s)],
        ['כתובת ציבורית', s.public_url || '—'], ['מאחורי reverse proxy', badge(s.behind_proxy)],
        ['טוקן ניהול מהסביבה', badge(s.admin_env_token_configured)], ['חשבונות ניהול', s.admin_accounts],
        ['כניסות ניהול פעילות', s.admin_sessions_active]])),
      card('מסד נתונים', rows([['מצב', badge(db.status)], ['בדיקת שלמות', db.integrity], ['קובץ', db.path_name],
        ['גודל', bytes(db.size_bytes)], ['WAL', bytes(db.wal_bytes)],
        ['גרסת סכמה', el('span', {}, `${db.schema_version} / ${db.schema_expected} `, badge(db.schema_up_to_date ? 'ok' : 'degraded'))]])),
      card('יומן ביקורת', rows([['שרשרת hash', badge(h.audit_chain.ok ? 'ok' : 'degraded')], ['אירועים ביומן', h.audit_chain.events],
        ['אירוע פגום ראשון', h.audit_chain.first_bad_event || '—']]),
      el('p', { class: 'muted small', text: 'append-only: טריגרים במסד חוסמים שינוי/מחיקה, והשרשרת מגלה שינוי בקובץ עצמו.' })),
      card('התראות ודחיפה', rows([['Web Push (VAPID)', badge(h.push.web_push_vapid)], ['VAPID subject מוגדר', badge(h.push.vapid_subject_set)],
        ['מכשירי Web Push', h.push.web_push_devices], ['FCM (אנדרואיד)', badge(h.push.fcm)], ['מכשירי FCM', h.push.fcm_devices],
        ['APNs', badge(h.push.apns)], ['זרם בזמן אמת (SSE)', badge(h.push.stream)],
        ['SMS', el('span', {}, badge(h.sms && h.sms.configured ? 'configured' : 'not_configured'), ` ${h.sms ? h.sms.provider : ''}`)],
        ['אימות טלפון ב-SMS', badge(!!(h.sms && h.sms.can_verify))], ['OSRM עצמי', badge(h.routing.osrm_configured)]])),
      card('מטמון נתונים חיצוניים', rows([['רשומות', h.data_cache.entries], ['נפח ערכים', bytes(h.data_cache.value_bytes)],
        ['קובץ', h.data_cache.file || 'בזיכרון'], ['גודל קובץ', bytes(h.data_cache.file_bytes)]]))));
  }

  // ---------------------------------------------------------------- טבלה עם סינון ומיון
  function dataTable({ columns, rowsData, filterKey, filterLabel, detail, searchKeys }) {
    let sortKey = null;
    let dir = 'asc';
    let q = '';
    let sel = '';
    const tbody = el('tbody');
    const count = el('span', { class: 'count' });
    const input = el('input', { type: 'search', placeholder: 'סינון חופשי…', 'aria-label': 'סינון חופשי' });
    const opts = filterKey ? [...new Set(rowsData.map((r) => r[filterKey]).filter(Boolean))].sort() : [];
    const select = filterKey ? el('select', { 'aria-label': filterLabel },
      el('option', { value: '', text: `${filterLabel}: הכל` }), opts.map((o) => el('option', { value: o, text: lab(o) }))) : null;
    const heads = columns.map((c) => {
      const b = el('button', { type: 'button', text: c.label });
      b.addEventListener('click', () => {
        dir = sortKey === c.key && dir === 'asc' ? 'desc' : 'asc';
        sortKey = c.key;
        heads.forEach((x) => x.removeAttribute('data-dir'));
        b.setAttribute('data-dir', dir);
        draw();
      });
      return b;
    });
    const raw = (r, c) => (c.sortValue ? c.sortValue(r) : r[c.key]);
    function draw() {
      const ql = q.trim().toLowerCase();
      let list = rowsData.filter((r) => (!sel || r[filterKey] === sel)
        && (!ql || (searchKeys || Object.keys(r)).some((k) => String(r[k] ?? '').toLowerCase().includes(ql))));
      if (sortKey) {
        const c = columns.find((x) => x.key === sortKey);
        list = list.slice().sort((a, b) => {
          const x = raw(a, c); const y = raw(b, c);
          const nx = parseFloat(x); const ny = parseFloat(y);
          const cmp = !Number.isNaN(nx) && !Number.isNaN(ny) && String(nx) === String(x).trim() && String(ny) === String(y).trim()
            ? nx - ny : String(x ?? '').localeCompare(String(y ?? ''), 'he');
          return dir === 'asc' ? cmp : -cmp;
        });
      }
      tbody.replaceChildren();
      for (const r of list) {
        const tr = el('tr', detail ? { class: 'clickable', tabindex: '0', 'aria-expanded': 'false' } : {},
          columns.map((c) => el('td', {}, c.render ? c.render(r) : String(r[c.key] ?? '—'))));
        tbody.append(tr);
        if (detail) {
          const dt = el('tr', { class: 'detail', hidden: true }, el('td', { colspan: String(columns.length) }, detail(r)));
          const toggle = () => { dt.hidden = !dt.hidden; tr.setAttribute('aria-expanded', String(!dt.hidden)); };
          tr.addEventListener('click', toggle);
          tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
          tbody.append(dt);
        }
      }
      count.textContent = `${list.length} מתוך ${rowsData.length}`;
    }
    input.addEventListener('input', () => { q = input.value; draw(); });
    if (select) select.addEventListener('change', () => { sel = select.value; draw(); });
    draw();
    return el('div', {}, el('div', { class: 'filter' }, input, select), count,
      el('div', { class: 'table-wrap' }, el('table', {},
        el('thead', {}, el('tr', {}, heads.map((b) => el('th', { scope: 'col' }, b)))), tbody)));
  }
  const kvList = (obj) => el('dl', { class: 'kv' }, Object.entries(obj).flatMap(([k, v]) => [
    el('dt', { text: k }), el('dd', { text: v === null || v === undefined || v === '' ? '—'
      : typeof v === 'object' ? JSON.stringify(v) : String(v) })]));

  // ---------------------------------------------------------------- מאגרי מידע
  function renderData() {
    const d = data.data;
    const box = $('tab-data');
    const yesNo = (v) => (v === true ? 'כן' : v === false ? 'לא' : v === null || v === undefined ? '—' : lab(String(v)));
    const srcTable = dataTable({
      rowsData: d.sources,
      filterKey: 'kind',
      filterLabel: 'סוג',
      searchKeys: ['id', 'name', 'kind_label', 'licence', 'coverage', 'state'],
      columns: [
        { key: 'name', label: 'מקור' },
        { key: 'kind_label', label: 'סוג' },
        { key: 'state', label: 'מצב', render: (r) => badge(r.state) },
        { key: 'licence', label: 'רישיון' },
        { key: 'terms_checked_at', label: 'תנאים נבדקו' },
        { key: 'key_env', label: 'מפתח', render: (r) => (r.key_env
          ? el('span', {}, el('code', { text: r.key_env }), ' ', badge(r.key_configured ? 'configured' : 'not_configured'))
          : 'לא נדרש') },
        { key: 'commercial_ok', label: 'מסחרי', render: (r) => yesNo(r.commercial_ok) },
      ],
      detail: (r) => kvList({ 'מזהה': r.id, 'אמינות': r.reliability, 'כיסוי': r.coverage, 'בשימוש ב': (r.used_by || []).join(', '),
        'תנאים': r.terms_url, 'שמיש בישראל': r.usable_in_israel, 'פירוט מצב': r.state_detail,
        'בדיקה אחרונה': r.last_check ? `${r.last_check.ok ? 'תקין' : 'נכשל'} · ${when(r.last_check.checked_at)} · ${r.last_check.detail || ''}` : 'לא נבדק' }),
    });
    const parts = [card('מקורות נתונים חיצוניים (server/data_sources.py)',
      el('p', { class: 'muted small', text: `${d.note} ערכי מפתחות לעולם לא מוצגים - רק שם המשתנה והאם הוגדר.` }), srcTable)];
    const c = d.catalog;
    if (!c.available) {
      parts.push(card('קטלוג הנתונים', el('p', { class: 'empty', text: `${c.note} (${c.path})` })));
    } else {
      const want = ['name_he', 'path', 'engine', 'kind', 'licence', 'size_mb', 'records', 'verdict', 'built_at'];
      const labels = { name_he: 'שם', path: 'נתיב', engine: 'מנוע', kind: 'סוג', licence: 'רישיון', size_mb: 'MB',
        records: 'רשומות', verdict: 'סטטוס', built_at: 'נבנה' };
      const cols = want.filter((k) => c.columns.includes(k)).map((k) => ({
        key: k, label: labels[k],
        render: k === 'path' ? (r) => el('code', { text: r.path }) : k === 'licence' ? (r) => String(r.licence || '—').slice(0, 90) + (String(r.licence || '').length > 90 ? '…' : '') : undefined,
      }));
      parts.push(card(`קטלוג הנתונים (${c.path})`,
        el('p', { class: 'muted small', text: `עודכן ${when(c.built_at)} · לחיצה על שורה מציגה את כל השדות.` }),
        dataTable({ rowsData: c.rows, columns: cols.length ? cols : c.columns.slice(0, 6).map((k) => ({ key: k, label: k })),
          filterKey: c.columns.includes('verdict') ? 'verdict' : (c.columns.includes('engine') ? 'engine' : null),
          filterLabel: c.columns.includes('verdict') ? 'סטטוס' : 'מנוע', detail: (r) => kvList(r) })));
    }
    box.replaceChildren(...parts);
  }

  // ---------------------------------------------------------------- איך המנוע עובד
  function valueNode(v, unit) {
    if (v === null || v === undefined) return el('span', { class: 'na', text: 'לא זמין' });
    if (Array.isArray(v)) return el('div', { class: 'chips' }, v.map((x) => el('span', { class: 'chip', text: Array.isArray(x) ? x.join(' ← ') : String(x) })));
    if (typeof v === 'object') {
      return el('div', { class: 'chips' }, Object.entries(v).map(([k, x]) => el('span', { class: 'chip',
        text: `${k}: ${Array.isArray(x) ? x.join(', ') : typeof x === 'object' && x !== null ? JSON.stringify(x) : x}` })));
    }
    if (typeof v === 'boolean') return el('span', { class: 'eng-value', text: v ? 'כן' : 'לא' });
    const human = unit === "שנ'" && typeof v === 'number' && v >= 120 ? ` (${dur(v)})` : '';
    return el('span', { class: 'eng-value', text: `${typeof v === 'number' ? num(v) : v}${unit ? ` ${unit}` : ''}${human}` });
  }

  function renderEngine() {
    const e = data.engine;
    const box = $('tab-engine');
    const cards = e.groups.map((g) => {
      const items = g.id === 'acoustic' && g.benchmark ? [acousticTable(g.benchmark)] : g.items.map((it) => {
        const simple = it.value === null || typeof it.value !== 'object';
        return el('div', { class: 'eng-item', 'data-key': it.key },
          el('div', { class: 'eng-head' }, el('span', { class: 'eng-label', text: it.label }), simple ? valueNode(it.value, it.unit) : null),
          simple ? null : valueNode(it.value, it.unit),
          it.desc ? el('div', { class: 'muted small', text: it.desc }) : null,
          el('div', { class: 'eng-src' }, el('code', { text: `${it.key}` }), ' · ', el('code', { text: it.source })));
      });
      return card(g.title, el('p', { class: 'eng-summary', text: g.summary }), ...items);
    });
    box.replaceChildren(el('p', { class: 'banner', text: e.note }), ...cards);
  }

  function acousticTable(b) {
    return el('div', {},
      b.summary ? el('p', { class: 'muted small', text: `קטעים: ${b.summary}` }) : null,
      b.detectors.map((d) => el('div', {}, el('h3', { text: d.detector }),
        el('div', { class: 'table-wrap' }, el('table', {},
          el('thead', {}, el('tr', {}, ['תקציב התראות שווא/שעה', 'סף', 'recall', 'רווח סמך 95%', 'שווא בפועל/שעה']
            .map((t) => el('th', { scope: 'col', text: t })))),
          el('tbody', {}, d.rows.map((r) => el('tr', {}, [r.budget_fa_per_h, r.threshold, r.recall, r.ci95, r.actual_fa_per_h]
            .map((x) => el('td', { text: String(x) }))))))))),
      el('p', { class: 'eng-src' }, el('code', { text: 'research/acoustic_benchmark_fsd50k.md' })));
  }

  // ---------------------------------------------------------------- הערות לשיפור
  function renderImprovements() {
    const im = data.improvements;
    const box = $('tab-improve');
    const cls = { good: 'ok', improve: 'warn', missing: 'bad' };
    const areas = { app: 'אפליקציה', data: 'מאגרי מידע', engine: 'מנוע' };
    let status = '';
    let area = '';
    const list = el('div');
    const statusPills = el('div', { class: 'pills', role: 'group', 'aria-label': 'סינון לפי מצב' });
    const areaPills = el('div', { class: 'pills', role: 'group', 'aria-label': 'סינון לפי תחום' });
    const draw = () => {
      statusPills.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === status)));
      areaPills.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === area)));
      const notes = im.notes.filter((n) => (!status || n.status === status) && (!area || n.area === area));
      list.replaceChildren(...(notes.length ? notes.map((n) => el('article', { class: 'note', 'data-id': n.id },
        el('div', { class: 'note-head' }, el('span', { class: `badge ${cls[n.status]}`, text: n.status_label }),
          el('h3', { class: 'note-title', text: n.title }), el('span', { class: 'chip', text: areas[n.area] || n.area })),
        el('dl', { class: 'kv note-kv' },
          el('dt', { text: 'מה נמדד' }), el('dd', { text: n.evidence }),
          el('dt', { text: 'למה זה חשוב' }), el('dd', { text: n.why }),
          el('dt', { text: 'הצעד הבא' }), el('dd', { text: n.next_step }),
          el('dt', { text: 'מקור' }), el('dd', {}, el('code', { text: n.source })))))
        : [el('p', { class: 'empty', text: 'אין הערות בסינון הזה' })]));
    };
    [['', `הכל (${im.notes.length})`], ['missing', `${im.labels.missing} (${im.counts.missing})`],
      ['improve', `${im.labels.improve} (${im.counts.improve})`], ['good', `${im.labels.good} (${im.counts.good})`]]
      .forEach(([v, t]) => statusPills.append(el('button', { type: 'button', 'data-v': v, text: t, onclick: () => { status = v; draw(); } })));
    [['', 'כל התחומים'], ...Object.entries(areas)]
      .forEach(([v, t]) => areaPills.append(el('button', { type: 'button', 'data-v': v, text: t, onclick: () => { area = v; draw(); } })));
    draw();
    box.replaceChildren(el('p', { class: 'banner', text: im.note }),
      el('div', { class: 'kpis' }, kpi(im.labels.missing, num(im.counts.missing), 'נתון או רכיב שלא קיים'),
        kpi(im.labels.improve, num(im.counts.improve), 'יש ראיה שהדיוק נפגע'), kpi(im.labels.good, num(im.counts.good), 'נבדק ותקין')),
      card('הערות לשיפור', statusPills, areaPills, list));
  }

  // ---------------------------------------------------------------- init
  document.addEventListener('DOMContentLoaded', () => {
    $('loginForm').addEventListener('submit', onLogin);
    $('logoutBtn').addEventListener('click', onLogout);
    $('refreshBtn').addEventListener('click', loadDashboard);
    document.querySelectorAll('.segmented [role=tab]').forEach((b) => b.addEventListener('click', () => selectTab(b.dataset.tab)));
    if (session()) loadDashboard(); else showLogin();
  });
}());
