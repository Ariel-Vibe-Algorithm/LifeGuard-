// api.js - thin client for the LifeGuard backend (/v1). Same origin, bearer token.
// The token is kept in localStorage (strict CSP: no inline/third-party scripts). It is never logged.

const TOKEN_KEY = 'lg.token';
const USER_KEY = 'lg.user';

export class ApiError extends Error {
  constructor(status, code, message, body) {
    super(message);
    this.status = status;
    this.code = code;
    this.body = body || {};
  }
  get offline() { return this.status === 0; }
}

let unauthorizedHandler = null;
let clockSkew = 0; // seconds: server clock - device clock (from the HTTP Date header)

export function onUnauthorized(fn) { unauthorizedHandler = fn; }

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* storage blocked */ } }

export const store = {
  get: lsGet,
  set: lsSet,
  getJSON(k, dflt = null) { try { const v = lsGet(k); return v ? JSON.parse(v) : dflt; } catch { return dflt; } },
  setJSON(k, v) { lsSet(k, v == null ? null : JSON.stringify(v)); },
};

export function getToken() { return lsGet(TOKEN_KEY); }
export function getUser() { return store.getJSON(USER_KEY); }
export function setSession(token, user) { lsSet(TOKEN_KEY, token); store.setJSON(USER_KEY, user); }
export function clearSession() { lsSet(TOKEN_KEY, null); lsSet(USER_KEY, null); }

/** Server "now" in epoch seconds (device clock corrected by the server's Date header). */
export function serverNow() { return Date.now() / 1000 + clockSkew; }

export async function api(method, path, body, { timeout = 15000 } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const token = getToken();
  if (token) headers.Authorization = 'Bearer ' + token;
  let res;
  const sentAt = Date.now();
  try {
    res = await fetch('/v1' + path, {
      method, headers, cache: 'no-store', credentials: 'omit', signal: ctl.signal,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(0, 'network', 'אין חיבור לשרת');
  } finally {
    clearTimeout(timer);
  }
  const date = res.headers.get('Date');
  if (date && Date.now() - sentAt < 3000) { // short requests only; long-poll responses are dated at the end
    const srv = Date.parse(date);
    if (!Number.isNaN(srv)) clockSkew = (srv - Date.now()) / 1000;
  }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const err = new ApiError(res.status, (data && data.error) || 'http_' + res.status,
      (data && data.message) || `שגיאת שרת (${res.status})`, data);
    if (res.status === 401 && path !== '/auth/login' && unauthorizedHandler) unauthorizedHandler(err);
    throw err;
  }
  return data;
}

// ------------------------------------------------------------------ volunteers beyond on-scene (server/helpers.py)
// Thin wrappers: the server decides eligibility, who is offered and who accepted. The UI only shows what they return.

/** GET my volunteer roles: {roles, languages, effective: {role: 'active' | reason}, labels}. */
export const volunteerRoles = () => api('GET', '/me/volunteer/roles');

/** PUT my roles (at least one of on_scene / remote_support / authority_liaison) + chat languages. */
export const setVolunteerRoles = (roles, languages) => api('PUT', '/me/volunteer/roles', { roles, languages });

/** Owner of an active incident asks for a remote_support / authority_liaison helper. Returns the owner view. */
export const requestHelper = (id, kind, reason = '') =>
  api('POST', `/incidents/${encodeURIComponent(id)}/request-helper`, { kind, reason });

/** Helper answers an offer: accept / decline / done / a liaison action (called_police ...). Returns the helper view. */
export const helperRespond = (id, action) =>
  api('POST', `/incidents/${encodeURIComponent(id)}/helper-respond`, { action });

/** Owner releases a helper (offer or accepted). Returns the owner view. */
export const releaseHelper = (id, helper) =>
  api('POST', `/incidents/${encodeURIComponent(id)}/helpers/${encodeURIComponent(helper)}/release`, {});

/** "I need to talk to someone" (not an emergency). Returns the existing open request with resumed:true if any. */
export const createSupportRequest = (note = '', language = 'he') =>
  api('POST', '/support-requests', { note, language });
