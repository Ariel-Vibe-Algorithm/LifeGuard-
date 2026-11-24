// envmap.js - "סביבה" map for the PWA: a vector canvas, no tiles, no third-party code.
// Why no basemap tiles: the server CSP allows only https://*.tile.openstreetmap.org (the OSM tile policy asks for the
// bare https://tile.openstreetmap.org host, which that wildcard does not match), the server sends
// Referrer-Policy: no-referrer (the policy asks for a valid Referer), and an emergency app should not depend on a
// donated tile service. What it draws instead: the street-level environment grid from GET /v1/map/environment
// (~100 m cells, sequential navy-blue scale - never red), my position, a destination, and walking routes.
// Interaction: drag to pan, pinch / wheel / +- to zoom, tap to pick a destination (when picking).

const ZOOMS = [0.004, 0.007, 0.012, 0.02];   // visible latitude span (degrees) per zoom step
const MAX_FETCH_SPAN = 0.0295;                // server: bbox <= 0.03 deg per side

function hexToRgb(h) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(h).trim());
  if (!m) return [128, 128, 128];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

/** Lighting scale cap: the 90th percentile of registered poles in view (at least 6), so dense city centres
 *  still show contrast. Exported for the legend. */
export function lightCap(env) {
  const v = ((env && env.cells) || []).map((c) => c.lighting).filter((x) => x != null).sort((a, b) => a - b);
  if (!v.length) return 6;
  return Math.max(6, v[Math.min(v.length - 1, Math.floor(v.length * .9))]);
}

/** value of one cell for the active layer -> 0..1 intensity (the more prominent colour = the factor is present:
 *  FEWER registered poles, more accidents, active nightlife, more reports), or null (no data: nothing drawn). */
function cellValue(layer, c, ctx) {
  if (layer === 'lighting') return c.lighting == null ? null : 1 - Math.min(1, c.lighting / ctx.lightCap);
  if (layer === 'accidents') {
    if (c.accidents == null) return null;
    if (c.accident_hotspot) return 1;
    return c.accidents ? Math.min(.85, .2 + .65 * c.accidents / Math.max(1, ctx.accMax)) : 0;
  }
  if (layer === 'nightlife') return c.nightlife == null ? null : (c.nightlife ? 1 : 0);
  if (layer === 'reports') return c.reports ? Math.min(1, .55 + .15 * c.reports) : null;
  return null;
}

export class EnvMap {
  constructor({ onTap, onViewChange } = {}) {
    this.onTap = onTap || (() => {});
    this.onViewChange = onViewChange || (() => {});
    this.center = { lat: 32.0853, lon: 34.7818 };
    this.zoom = 1;
    this.span = ZOOMS[this.zoom];
    this.env = null;
    this.layer = 'lighting';
    this.me = null;
    this.dest = null;
    this.routes = [];          // [{points: [[lat, lon]...], selected}]
    this.picking = false;

    this.frame = document.createElement('div');
    this.frame.className = 'map-frame';
    this.canvas = document.createElement('canvas');
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', 'מפת סביבה');
    this.canvas.dataset.test = 'env-canvas';
    this.status = document.createElement('div');
    this.status.className = 'map-status';
    this.status.setAttribute('role', 'status');
    this.status.dataset.test = 'map-status';
    this.attr = document.createElement('div');
    this.attr.className = 'map-attr';
    this.attr.dataset.test = 'map-attribution';
    this.frame.append(this.canvas, this.status, this.attr);

    this._ptrs = new Map();
    this._moved = false;
    this._pinch = null;
    this._bind();
    this._ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => this.draw()) : null;
    if (this._ro) this._ro.observe(this.frame);
    this._mq = matchMedia('(prefers-color-scheme: dark)');
    this._onScheme = () => this.draw();
    try { this._mq.addEventListener('change', this._onScheme); } catch { /* old Safari */ }
  }

  destroy() {
    if (this._ro) this._ro.disconnect();
    try { this._mq.removeEventListener('change', this._onScheme); } catch { /* ignore */ }
    clearTimeout(this._viewTimer);
  }

  // ---------------- geometry ----------------
  _size() { return { w: this.frame.clientWidth || 360, h: this.frame.clientHeight || 340 }; }
  _k() { return this._size().h / this.span; }                       // px per degree latitude
  _cos() { return Math.cos(this.center.lat * Math.PI / 180); }
  project(lat, lon) {
    const { w, h } = this._size();
    const k = this._k();
    return [w / 2 + (lon - this.center.lon) * this._cos() * k, h / 2 - (lat - this.center.lat) * k];
  }
  unproject(x, y) {
    const { w, h } = this._size();
    const k = this._k();
    return { lat: this.center.lat - (y - h / 2) / k, lon: this.center.lon + (x - w / 2) / (k * this._cos()) };
  }
  bbox() {
    const { w, h } = this._size();
    const a = this.unproject(0, h), b = this.unproject(w, 0);
    return [a.lat, a.lon, b.lat, b.lon];
  }
  /** the area to ask the server for: the visible box, capped to the server limit around the centre. */
  fetchBbox() {
    let [s, w, n, e] = this.bbox();
    const cap = (lo, hi, mid) => (hi - lo > MAX_FETCH_SPAN ? [mid - MAX_FETCH_SPAN / 2, mid + MAX_FETCH_SPAN / 2] : [lo, hi]);
    [s, n] = cap(s, n, this.center.lat);
    [w, e] = cap(w, e, this.center.lon);
    return [s, w, n, e].map((v) => Number(v.toFixed(5)));
  }

  setCenter(lat, lon) { this.center = { lat, lon }; this.draw(); }
  zoomBy(step) {
    const z = Math.max(0, Math.min(ZOOMS.length - 1, this.zoom + step));
    if (z === this.zoom) return;
    this.zoom = z; this.span = ZOOMS[z];
    this.draw(); this._viewChanged();
  }
  canZoom(step) { const z = this.zoom + step; return z >= 0 && z < ZOOMS.length; }
  /** fit points ([lat, lon]) with a margin, within the zoom range. */
  fit(points) {
    if (!points.length) return;
    let s = 90, n = -90, w = 180, e = -180;
    for (const [la, lo] of points) { s = Math.min(s, la); n = Math.max(n, la); w = Math.min(w, lo); e = Math.max(e, lo); }
    this.center = { lat: (s + n) / 2, lon: (w + e) / 2 };
    const { w: W, h: H } = this._size();
    const needLat = Math.max(n - s, (e - w) * this._cos() * H / W) * 1.35;
    let z = ZOOMS.findIndex((v) => v >= needLat);
    if (z < 0) z = ZOOMS.length - 1;
    this.zoom = z; this.span = Math.max(ZOOMS[z], Math.min(needLat, 0.03));
    this.draw();
  }

  _viewChanged() {
    clearTimeout(this._viewTimer);
    this._viewTimer = setTimeout(() => this.onViewChange(this.fetchBbox()), 350);
  }

  // ---------------- input ----------------
  _bind() {
    const c = this.canvas;
    c.addEventListener('pointerdown', (ev) => {
      c.setPointerCapture?.(ev.pointerId);
      this._ptrs.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (this._ptrs.size === 1) { this._moved = false; this._start = { x: ev.clientX, y: ev.clientY }; }
      if (this._ptrs.size === 2) {
        const [a, b] = [...this._ptrs.values()];
        this._pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), span: this.span };
        this._moved = true;
      }
    });
    c.addEventListener('pointermove', (ev) => {
      const prev = this._ptrs.get(ev.pointerId);
      if (!prev) return;
      const cur = { x: ev.clientX, y: ev.clientY };
      this._ptrs.set(ev.pointerId, cur);
      if (this._ptrs.size === 2 && this._pinch) {
        const [a, b] = [...this._ptrs.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        this.span = Math.max(ZOOMS[0] * .8, Math.min(0.026, this._pinch.span * this._pinch.d / d));
        this.zoom = ZOOMS.reduce((best, v, i) => (Math.abs(v - this.span) < Math.abs(ZOOMS[best] - this.span) ? i : best), 0);
        this.draw();
        return;
      }
      if (!this._moved && Math.hypot(cur.x - this._start.x, cur.y - this._start.y) < 6) return;
      this._moved = true;
      const k = this._k();
      this.center = { lat: this.center.lat + (cur.y - prev.y) / k, lon: this.center.lon - (cur.x - prev.x) / (k * this._cos()) };
      this.draw();
    });
    const end = (ev) => {
      if (!this._ptrs.has(ev.pointerId)) return;
      this._ptrs.delete(ev.pointerId);
      if (this._ptrs.size < 2) this._pinch = null;
      if (this._ptrs.size) return;
      if (this._moved) { this._viewChanged(); return; }
      if (ev.type === 'pointerup') {
        const r = c.getBoundingClientRect();
        this.onTap(this.unproject(ev.clientX - r.left, ev.clientY - r.top));
      }
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('wheel', (ev) => { ev.preventDefault(); this.zoomBy(ev.deltaY > 0 ? 1 : -1); }, { passive: false });
  }

  // ---------------- drawing ----------------
  _colors() {
    const cs = getComputedStyle(this.frame);
    const v = (n) => cs.getPropertyValue(n).trim();
    return {
      ground: v('--map-ground') || '#EEF2F8', grid: v('--map-grid') || 'rgba(10,31,68,.07)',
      low: hexToRgb(v('--map-low')), mid: hexToRgb(v('--map-mid')), high: hexToRgb(v('--map-high')),
      accent: v('--accent') || '#0B63E5', surface: v('--surface') || '#fff', brand: v('--brand') || '#0A1F44',
      label2: v('--label-2') || '#47546A', label3: v('--label-3') || '#5F6B80',
    };
  }

  draw() {
    const { w, h } = this._size();
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    const c = this.canvas;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const g = c.getContext('2d');
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const col = this._colors();
    g.fillStyle = col.ground;
    g.fillRect(0, 0, w, h);
    this.frame.classList.toggle('picking', !!this.picking);

    // faint ~100 m grid (orientation without a basemap)
    const cellDeg = (this.env && this.env.cell_deg) || 0.001;
    const k = this._k();
    const cellPxY = cellDeg * k;
    if (cellPxY >= 8) {
      g.strokeStyle = col.grid; g.lineWidth = 1;
      const [s, wl, n, e] = this.bbox();
      g.beginPath();
      for (let la = Math.floor(s / cellDeg) * cellDeg; la <= n; la += cellDeg) { const y = Math.round(this.project(la, wl)[1]) + .5; g.moveTo(0, y); g.lineTo(w, y); }
      for (let lo = Math.floor(wl / cellDeg) * cellDeg; lo <= e; lo += cellDeg) { const x = Math.round(this.project(s, lo)[0]) + .5; g.moveTo(x, 0); g.lineTo(x, h); }
      g.stroke();
    }

    // environment cells - sequential navy-blue, never red
    if (this.env && Array.isArray(this.env.cells)) {
      const accMax = Math.max(1, ...this.env.cells.map((x) => x.accidents || 0));
      const cap = lightCap(this.env);
      const cw = cellDeg * k * this._cos(), ch = cellPxY;
      const gap = ch > 14 ? 1 : .5;
      const rad = ch > 24 ? 3 : 0;
      for (const cell of this.env.cells) {
        const t = cellValue(this.layer, cell, { accMax, lightCap: cap });
        if (t == null) continue;
        const [x, y] = this.project(cell.lat, cell.lon);
        if (x < -cw || y < -ch || x > w + cw || y > h + ch) continue;
        const rgb = t < .5 ? mix(col.low, col.mid, t * 2) : mix(col.mid, col.high, (t - .5) * 2);
        g.fillStyle = rgba(rgb, .88);
        const rx = x - cw / 2 + gap / 2, ry = y - ch / 2 + gap / 2, rw = cw - gap, rh = ch - gap;
        if (rad && g.roundRect) { g.beginPath(); g.roundRect(rx, ry, rw, rh, rad); g.fill(); } else g.fillRect(rx, ry, rw, rh);
        if (this.layer === 'accidents' && cell.accident_hotspot && ch > 10) {
          g.strokeStyle = col.surface; g.lineWidth = 1.5;
          g.beginPath(); g.arc(x, y, Math.min(cw, ch) * .22, 0, Math.PI * 2); g.stroke();
        }
      }
    }

    // routes: others thin, the chosen one with a casing
    const line = (pts) => { g.beginPath(); pts.forEach(([la, lo], i) => { const [x, y] = this.project(la, lo); if (i) g.lineTo(x, y); else g.moveTo(x, y); }); };
    g.lineJoin = 'round'; g.lineCap = 'round';
    for (const r of this.routes.filter((x) => !x.selected)) {
      line(r.points); g.strokeStyle = col.surface; g.lineWidth = 7; g.globalAlpha = .9; g.stroke();
      line(r.points); g.strokeStyle = col.label3; g.lineWidth = 4; g.globalAlpha = .75; g.stroke();
      g.globalAlpha = 1;
    }
    for (const r of this.routes.filter((x) => x.selected)) {
      line(r.points); g.strokeStyle = col.surface; g.lineWidth = 10; g.stroke();
      line(r.points); g.strokeStyle = col.accent; g.lineWidth = 6; g.stroke();
    }

    // destination: a navy pin
    if (this.dest) {
      const [x, y] = this.project(this.dest.lat, this.dest.lon);
      g.fillStyle = col.brand; g.strokeStyle = col.surface; g.lineWidth = 2.5;
      g.beginPath();
      g.moveTo(x, y);
      g.bezierCurveTo(x - 4, y - 8, x - 11, y - 12, x - 11, y - 21);
      g.arc(x, y - 21, 11, Math.PI, 0);
      g.bezierCurveTo(x + 11, y - 12, x + 4, y - 8, x, y);
      g.closePath(); g.fill(); g.stroke();
      g.fillStyle = col.surface; g.beginPath(); g.arc(x, y - 21, 4, 0, Math.PI * 2); g.fill();
    }

    // me: accuracy halo + dot with a white ring
    if (this.me) {
      const [x, y] = this.project(this.me.lat, this.me.lon);
      const acc = Math.min(160, (this.me.accuracy_m || 0) / 111000 * k);
      if (acc > 9) { g.fillStyle = col.accent; g.globalAlpha = .14; g.beginPath(); g.arc(x, y, acc, 0, Math.PI * 2); g.fill(); g.globalAlpha = 1; }
      g.fillStyle = col.surface; g.beginPath(); g.arc(x, y, 10, 0, Math.PI * 2); g.fill();
      g.fillStyle = col.accent; g.beginPath(); g.arc(x, y, 7, 0, Math.PI * 2); g.fill();
    }

    this._drawScale(g, w, h, col);
  }

  _drawScale(g, w, h, col) {
    const mPerPx = this.span * 111000 / h;
    const nice = [50, 100, 200, 250, 500, 1000];
    const m = nice.find((v) => v / mPerPx >= 56) || 1000;
    const px = m / mPerPx;
    const x0 = 22, x1 = x0 + px, y = h - 40;
    const plate = (x, yy, ww, hh) => {
      g.fillStyle = col.surface; g.globalAlpha = .86;
      g.beginPath(); if (g.roundRect) g.roundRect(x, yy, ww, hh, 8); else g.rect(x, yy, ww, hh); g.fill();
      g.globalAlpha = 1;
    };
    plate(x0 - 10, y - 24, px + 20, 32);
    plate(12, 14, 36, 48);
    g.strokeStyle = col.label2; g.lineWidth = 2; g.lineCap = 'round';
    g.beginPath(); g.moveTo(x0, y - 4); g.lineTo(x0, y); g.lineTo(x1, y); g.lineTo(x1, y - 4); g.stroke();
    g.font = '600 12px -apple-system, "Segoe UI", Arial, sans-serif';
    g.fillStyle = col.label2; g.textAlign = 'center';
    g.fillText(m >= 1000 ? `${m / 1000} ק"מ` : `${m} מ'`, (x0 + x1) / 2, y - 8);
    // north indicator
    g.textAlign = 'center';
    g.fillText('צפון', 30, 56);
    g.beginPath(); g.moveTo(30, 20); g.lineTo(23, 38); g.lineTo(30, 33); g.lineTo(37, 38); g.closePath();
    g.fillStyle = col.brand; g.fill();
  }
}
