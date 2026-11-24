// hazard_core.js - AI hazard alerts: the decision logic, without any DOM. A port of hazard_alerts.py (AlertManager
// grouping / cooldown / escalation / rate limit, severity ranks, families, prefs defaults, he/en strings, false-alarm
// reasons, contacts message) and hazard_monitor.py (detections from a window report, severity filter, distress head,
// the "repeated shouts" sequence rule, not-shown reasons). Same constants as Windows - checked by the parity test.
// Safety: a detection NEVER starts SOS, never contacts anyone and never dials - it can only offer.

export const EVENT_GAP_S = 120;
export const COOLDOWN_DISMISS_S = 5 * 60;
export const COOLDOWN_FALSE_S = 15 * 60;
export const COOLDOWN_ACTION_S = 10 * 60;
export const MIN_GAP_ANY_S = 20;
export const WINDOW_S = 1.5;

export const SEVERITY_RANK = { info: 0, low: 1, medium: 2, high: 3 };
export const MIN_SEVERITY_OPTIONS = { high: 'רק סכנה גבוהה', medium: 'בינונית ומעלה (מומלץ)', low: 'כל זיהוי' };
export const MIN_SEVERITY_OPTIONS_EN = { high: 'High only', medium: 'Medium and up (recommended)', low: 'Every detection' };
export const FAMILY = {
  gunshot: 'blast', explosion: 'blast', fireworks: 'blast', scream: 'voice',
  distress_voice: 'voice', glass_break: 'break', impact: 'break', alarm: 'signal', siren: 'signal',
};
export const FALSE_REASONS = { music_tv: 'מוזיקה / טלוויזיה', fireworks: 'זיקוקים', horn: 'צופר רכב', construction: 'עבודות / בנייה', other: 'אחר' };
export const RESPONSES = ['sos', 'safe_place', 'contacts', 'call', 'false_alarm', 'dismiss'];
// hazard_alerts.DEFAULTS + the PWA-only foreground listening switch (default OFF)
export const DEFAULTS = { popups: true, sound: true, vibration: false, min_severity: 'medium', language: 'he' };
export const PWA_DEFAULTS = { ...DEFAULTS, listen: false };

export const STRINGS = {
  he: {
    title: 'LifeGuard זיהה סכנה אפשרית', question: 'מה תרצה/י לעשות?', sos: '🚨 הפעלת SOS',
    safe_place: '📍 מקום בטוח', contacts: '👨‍👩‍👧 התראה לאנשי הקשר', call: '📞 חיוג לחירום',
    false_alarm: 'התראת שווא', dismiss: 'סגירה', severity: 'חומרה', confidence: 'ביטחון בזיהוי',
    location: 'מיקום', no_location: 'מיקום לא ידוע', updated: 'עודכן - זוהה שוב',
    sev: { high: 'גבוהה', medium: 'בינונית', low: 'נמוכה', info: 'מידע' },
    confirm_sos: 'להפעיל עכשיו SOS? (ספירה לאחור עם אפשרות ביטול, כמו לחיצה על הכפתור)',
    confirm_contacts: 'לשלוח לאנשי הקשר הודעה שזוהתה סכנה אפשרית? ההודעה תציין שזה זיהוי AI שלא אומת.',
    confirm_call: 'לפתוח חיוג ל-{n}?', why: 'למה זו התראת שווא? (לא חובה)',
    not_verified: 'זיהוי של צליל בלבד - לא אישור לסכנה', dir: 'rtl',
    not_connected: 'לא מחובר לשרת', sending: 'שולח...', approx: 'משוער', min_ago: 'לפני {m} דק\'',
    no_contacts: 'אין אנשי קשר מאושרים במעגל הביטחון - ההודעה לא נשלחה לאף אחד',
    sent: 'השרת שלח ל-{n} אנשי קשר. ממתין לאישור קבלה באפליקציות...',
    delivered: '{ok} מתוך {n} אישרו קבלה באפליקציה', send_failed: '❌ ההודעה לא נשלחה: {e}',
    dial_opened: 'נפתחה אפליקציית החיוג ל-{n} (אם מקושר טלפון). אם לא - חייגו מהטלפון.',
    no_dialer: 'אין במחשב הזה אפליקציית חיוג. חייגו {n} מהטלפון.',
    // PWA-only
    yes: 'כן', no: 'לא, חזרה', dial: 'חיוג {n}', dial_hint: 'הקישו כדי לחייג. אם אין במכשיר חיוג - חייגו {n} מהטלפון.',
    safe_na: 'חיפוש מקום בטוח לא זמין באפליקציית הדפדפן. נפתחה מפת הסביבה (תאורה, תאונות, דיווחים) - היא לא מצביעה על מקום בטוח.',
    sos_started: 'נשלחת קריאת חירום - עוברים למסך ה-SOS...', false_saved: 'נרשם כהתראת שווא (משוב לא מאומת, לא משנה ספים).',
    send_false: 'סימון כהתראת שווא', offline: 'אין חיבור לשרת', close: 'סגירה', ok: 'אישור',
    notif_body: '{label} · {sev}. זיהוי של צליל בלבד - פתחו את LifeGuard.',
  },
  en: {
    title: 'LifeGuard detected a potential danger', question: 'What would you like to do?',
    sos: '🚨 Activate SOS', safe_place: '📍 Find a safe place', contacts: '👨‍👩‍👧 Alert contacts',
    call: '📞 Call emergency', false_alarm: 'False alarm', dismiss: 'Dismiss', severity: 'Severity',
    confidence: 'Detection confidence', location: 'Location', no_location: 'Location unknown',
    updated: 'Updated - detected again',
    sev: { high: 'High', medium: 'Medium', low: 'Low', info: 'Info' },
    confirm_sos: 'Start SOS now? (countdown with cancel, same as pressing the button)',
    confirm_contacts: 'Send your contacts a message that a potential danger was detected? It will say this is an unverified AI detection.',
    confirm_call: 'Open the dialer for {n}?', why: 'Why is this a false alarm? (optional)',
    not_verified: 'Sound-only detection - not a confirmation of danger', dir: 'ltr',
    not_connected: 'not connected to server', sending: 'Sending...', approx: 'approximate',
    min_ago: '{m} min ago', no_contacts: 'No approved contacts in your safety circle - nothing was sent',
    sent: 'The server sent it to {n} contacts. Waiting for their apps to confirm receipt...',
    delivered: '{ok} of {n} confirmed receipt in the app', send_failed: '❌ Not sent: {e}',
    dial_opened: 'Opened the dialer for {n} (if a phone is linked). Otherwise dial from your phone.',
    no_dialer: 'This computer has no dialer app. Dial {n} from your phone.',
    yes: 'Yes', no: 'No, back', dial: 'Dial {n}', dial_hint: 'Tap to dial. If this device cannot call, dial {n} from your phone.',
    safe_na: 'Safe-place search is not available in the web app. The environment map (lighting, accidents, reports) is open - it does not point to a safe place.',
    sos_started: 'Sending the emergency call - opening the SOS screen...', false_saved: 'Recorded as a false alarm (unverified feedback; thresholds are not changed).',
    send_false: 'Mark as false alarm', offline: 'not connected to server', close: 'Close', ok: 'OK',
    notif_body: '{label} · {sev}. Sound-only detection - open LifeGuard.',
  },
};
export const LABELS_EN = {
  gunshot: 'Gunshot', explosion: 'Explosion', fireworks: 'Fireworks', glass_break: 'Glass breaking',
  siren: 'Siren', alarm: 'Alarm', scream: 'Scream', impact: 'Heavy impact', distress_voice: 'Distress voice (scream / shout / crying)',
};
export const EXPLAIN_EN = {
  gunshot: 'A sound similar to a gunshot. Fireworks and drums can sound alike.',
  explosion: 'A sound similar to an explosion. Fireworks and thunder can sound alike.',
  alarm: 'An alarm sound. Car horns are sometimes detected as alarms.',
  scream: 'A sound similar to a scream. Happy shouting and play sound alike.',
  distress_voice: 'Sounds like screaming, shouting or crying. Happy shouting and play sound alike.',
  glass_break: 'A glass-breaking sound.', impact: 'A heavy impact.',
};
export const REASONS_EN = { music_tv: 'Music / TV', fireworks: 'Fireworks', horn: 'Vehicle horn', construction: 'Construction', other: 'Other' };

export const fmt = (s, vars) => String(s).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? String(vars[k]) : m));

// ------------------------------------------------------------------ prefs (validation like hazard_alerts.save_prefs)
export function cleanPrefs(p, { canVibrate = true } = {}) {
  const out = { ...PWA_DEFAULTS };
  for (const k of Object.keys(PWA_DEFAULTS)) if (p && p[k] !== undefined) out[k] = p[k];
  for (const k of ['popups', 'sound', 'vibration', 'listen']) out[k] = !!out[k];
  if (!(out.min_severity in MIN_SEVERITY_OPTIONS)) out.min_severity = DEFAULTS.min_severity;
  if (!(out.language in STRINGS)) out.language = DEFAULTS.language;
  if (!canVibrate) out.vibration = false;               // like Windows: never keep a value that cannot happen
  return out;
}

export function severityAllowed(severity, prefs) {
  return (SEVERITY_RANK[severity] || 0) >= SEVERITY_RANK[(prefs && prefs.min_severity) || 'medium'];
}

function hexId() {
  const b = new Uint8Array(6);
  (globalThis.crypto || {}).getRandomValues ? crypto.getRandomValues(b) : b.forEach((_, i) => { b[i] = Math.floor(Math.random() * 256); });
  return [...b].map((v) => v.toString(16).padStart(2, '0')).join('');
}

// ------------------------------------------------------------------ AlertManager (hazard_alerts.AlertManager)
export class AlertManager {
  constructor(clock = () => Date.now() / 1000) {
    this.clock = clock;
    this.open = {};          // family -> alert
    this.cooldown = {};      // family -> [until, severity_rank]
    this.lastPopup = 0;
  }

  /** det: {category, severity, confidence, label_he, explanation, meta} -> [kind, alert|null] */
  decide(det) {
    const now = this.clock();
    const fam = FAMILY[det.category] || det.category;
    const rank = SEVERITY_RANK[det.severity] || 0;
    const a = this.open[fam];
    if (a && now - a.last_ts <= EVENT_GAP_S) {
      a.count += 1;
      a.last_ts = now;
      if (rank > (SEVERITY_RANK[a.severity] || 0)) {
        Object.assign(a, { category: det.category, severity: det.severity, confidence: Math.max(a.confidence, det.confidence), label_he: det.label_he ?? null, explanation: det.explanation ?? null });
        return ['update', a];
      }
      a.confidence = Math.max(a.confidence, det.confidence);
      return ['grouped', a];
    }
    const cd = this.cooldown[fam];
    if (cd && now < cd[0] && rank <= cd[1]) return ['cooldown', null];
    if (now - this.lastPopup < MIN_GAP_ANY_S && !Object.values(this.open).some((o) => rank > (SEVERITY_RANK[o.severity] || 0))) {
      return ['rate_limited', null];
    }
    const al = {
      id: 'hz-' + hexId(), family: fam, category: det.category, severity: det.severity, confidence: det.confidence,
      label_he: det.label_he ?? null, explanation: det.explanation ?? null, first_ts: now, last_ts: now, count: 1,
      response: null, meta: det.meta || {},
    };
    this.open[fam] = al;
    this.lastPopup = now;
    return ['new', al];
  }

  /** the user's response closes the event, with a cooldown by response type */
  close(alert, response) {
    const now = this.clock();
    const secs = { false_alarm: COOLDOWN_FALSE_S, dismiss: COOLDOWN_DISMISS_S }[response] ?? COOLDOWN_ACTION_S;
    alert.response = response;
    delete this.open[alert.family];
    this.cooldown[alert.family] = [now + secs, SEVERITY_RANK[alert.severity] || 0];
  }
}

// ------------------------------------------------------------------ hazard_monitor.HazardMonitor (after analysis)
const EXPLAIN_BASE = {
  gunshot: 'צליל שדומה לירי', explosion: 'צליל שדומה לפיצוץ', glass_break: 'צליל של שבירת זכוכית',
  alarm: 'צליל של אזעקה', scream: 'צליל שדומה לצרחה', impact: 'חבטה חזקה', fireworks: 'צליל שדומה לזיקוקים', siren: 'סירנה',
};
const EXPLAIN_NOTE = {
  gunshot: 'זיקוקים ותופים יכולים להישמע דומה', explosion: 'זיקוקים ורעם יכולים להישמע דומה',
  alarm: 'צופר רכב לפעמים מזוהה כאזעקה', scream: 'צעקות שמחה ומשחק נשמעות דומה',
};

export function explain(e) {
  const base = EXPLAIN_BASE[e.category] ?? (e.label_he || '');
  const note = EXPLAIN_NOTE[e.category];
  const near = e.near_threshold ? ' (קרוב לסף - לא ודאי)' : '';
  return base + near + (note ? `. ${note}` : '');
}

export function mergeEvents(reps) {
  const best = new Map();
  for (const r of reps) {
    for (const e of r.events || []) {
      const cur = best.get(e.category);
      if (!cur || (e.margin || 0) > (cur.margin || 0)) best.set(e.category, e);
    }
  }
  return [...best.values()];
}

export class MonitorCore {
  /** sequenceRule: models/shout_sequence.json (or null = rule off); canShow: () => [ok, reason]; prefsFn: () => prefs */
  constructor({ sequenceRule = null, canShow = () => [true, null], prefsFn = () => ({ ...DEFAULTS }), clock, record, onAlert } = {}) {
    this.sequenceRule = sequenceRule && ['k', 't_low', 's_low', 'span_s'].every((k) => k in sequenceRule) ? sequenceRule : null;
    this.canShow = canShow;
    this.prefsFn = prefsFn;
    this.manager = new AlertManager(clock);
    this.record = record || (() => {});
    this.onAlert = onAlert || (() => {});
    this.soft = [];
    this.seqConf = 0;
    this.stats = { windows: 0, analyzed: 0, detections: 0 };
  }

  /** reports: one per 1.5 s window, in time order (null = unavailable / failed). -> [{kind, alert, show, why}] */
  process(reports, now) {
    const valid = [];
    reports.forEach((r, j) => {
      this.stats.analyzed += 1;
      if (!r || (r.quality || {}).status === 'unavailable') return;
      valid.push(r);
      this.noteSoft(r, now - (reports.length - 1 - j) * WINDOW_S);
    });
    if (!valid.length) return [];
    let rep = valid[0];
    const key = (r) => [(r.events || []).length, (r.distress || {}).detected ? 1 : 0];
    for (const r of valid.slice(1)) {
      const a = key(r), b = key(rep);
      if (a[0] > b[0] || (a[0] === b[0] && a[1] > b[1])) rep = r;
    }
    rep = { ...rep, events: mergeEvents(valid) };
    if (valid.some((r) => (r.distress || {}).detected)) {
      let best = null;
      for (const r of valid) {
        const d = r.distress || {};
        if (!best || (d.score || -1e9) > (best.score || -1e9)) best = d;
      }
      rep.distress = best;
    }
    const dets = [];
    for (const e of rep.events || []) {
      if ((SEVERITY_RANK[e.severity_prior] || 0) < SEVERITY_RANK.low) continue;   // "info" (siren): no popup
      dets.push({
        category: e.category, severity: e.severity_prior, confidence: Number(e.detection_confidence || 0), label_he: e.label_he ?? null,
        explanation: explain(e),
        meta: {
          score: e.score ?? null, threshold: e.threshold ?? null, model: e.model ?? null, model_version: rep.model_version ?? null,
          quality: (rep.quality || {}).status ?? null, near_threshold: !!e.near_threshold,
        },
      });
    }
    const d = rep.distress || {};
    if (d.detected) {
      dets.push({
        category: 'distress_voice', severity: 'medium', confidence: Number(d.probability || 0), label_he: 'קול מצוקה (צרחה / צעקה / בכי)',
        explanation: 'צליל שדומה לצרחה, צעקה או בכי. צעקות שמחה ומשחק נשמעות דומה.',
        meta: { score: d.score ?? null, model_version: rep.model_version ?? null },
      });
    }
    if (!d.detected && this.sequenceFired(now)) {
      const k = this.sequenceRule.k;
      dets.push({
        category: 'distress_voice', severity: 'medium', confidence: this.seqConf, label_he: 'צעקות חוזרות',
        explanation: `${k} קטעי קול דמויי צעקה בתוך שניות ספורות. צעקות שמחה ומשחק נשמעות דומה.`,
        meta: { rule: this.sequenceRule.version ?? null, model_version: rep.model_version ?? null },
      });
    }
    const out = [];
    const prefs = this.prefsFn();
    for (const det of dets) {
      this.stats.detections += 1;
      const [kind, alert] = this.manager.decide(det);
      if (!alert) continue;                 // cooldown after a response / rate limit: not recorded again
      let show = true, why = null;
      if (!prefs.popups) { show = false; why = 'popups_disabled'; } else if (!severityAllowed(alert.severity, prefs)) { show = false; why = 'below_min_severity'; } else {
        const [ok, reason] = this.canShow();
        if (!ok) { show = false; why = reason; }
      }
      if (kind === 'new' || kind === 'update') {
        try { this.record(alert, show, why); } catch { /* the log never blocks an alert */ }
        if (show) this.onAlert(kind, alert);
      }
      out.push({ kind, alert, show, why });
    }
    return out;
  }

  noteSoft(rep, ts) {
    const rule = this.sequenceRule;
    if (!rule) return;
    const d = (rep.distress || {}).score;
    const s = (rep.category_scores || {}).scream;
    const hit = (d != null && d >= rule.t_low) || (s != null && s >= rule.s_low);
    const prob = Number((rep.distress || {}).probability || 0);
    this.soft.push([ts, !!hit, prob]);
    this.soft = this.soft.filter((e) => ts - e[0] <= rule.span_s * 2).slice(-6);
  }

  sequenceFired(now) {
    const rule = this.sequenceRule;
    if (!rule) return false;
    const hits = this.soft.filter((e) => e[1] && now - e[0] <= rule.span_s * 2);
    if (hits.length < rule.k) return false;
    const probs = hits.map((h) => h[2]).sort((a, b) => a - b);
    this.seqConf = probs[Math.floor(hits.length / 2)];
    this.soft = [];                         // the same sequence is not counted twice
    return true;
  }
}

/** hazard_alerts.contacts_message - the text the server sends to the safety circle. */
export function contactsMessage(alert, lang = 'he') {
  const s = STRINGS[lang] || STRINGS.he;
  const label = alert.label_he || alert.category;
  const d = new Date(alert.first_ts * 1000);
  const when = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (lang === 'en') {
    return `LifeGuard: a possible danger sound (${alert.category}) was detected near me at ${when}. This is an unverified AI detection. Please check on me.`;
  }
  return `LifeGuard: זוהה אצלי צליל שעשוי להעיד על סכנה (${label}) בשעה ${when}. ${s.not_verified}. כדאי לבדוק מה שלומי.`;
}
