// icons.js - LifeGuard's vector icon set (24x24, SF-Symbols-like line icons). Inline SVG built with the DOM API:
// crisp at any pixel density, themable with currentColor, no network and nothing inline for the CSP to block.
const NS = 'http://www.w3.org/2000/svg';

// each icon: list of [tag, attrs]; stroke icons by default, `fill: 1` marks a filled shape
const P = (d, extra) => ['path', { d, ...extra }];
const ICONS = {
  shield: [P('M12 2.8 4.5 5.8v5.6c0 4.8 3.2 8.6 7.5 9.8 4.3-1.2 7.5-5 7.5-9.8V5.8z')],
  'shield-check': [P('M12 2.8 4.5 5.8v5.6c0 4.8 3.2 8.6 7.5 9.8 4.3-1.2 7.5-5 7.5-9.8V5.8z'), P('m8.8 12.2 2.3 2.3 4.3-4.6')],
  chat: [P('M4 5.5h16a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1h-9l-4.5 3.5V17H4a1 1 0 0 1-1-1V6.5a1 1 0 0 1 1-1z')],
  walk: [['circle', { cx: 13.5, cy: 4.3, r: 1.8 }], P('M10.6 21l1.8-6.2 2.6 2.5V21'), P('M8 12.3l1.6-4.1 3.7-1.2 2.1 3.6 2.6 1.3'), P('M12.4 14.8 13.3 7')],
  check: [['circle', { cx: 12, cy: 12, r: 9 }], P('m8 12.3 2.8 2.8L16.2 9.5')],
  checklist: [P('M9.5 6.5H20M9.5 12H20M9.5 17.5H20'), P('m3.6 6.4 1.4 1.4 2.4-2.6M3.6 11.9l1.4 1.4 2.4-2.6M3.6 17.4l1.4 1.4 2.4-2.6')],
  eye: [P('M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z'), ['circle', { cx: 12, cy: 12, r: 3 }]],
  people: [['circle', { cx: 9, cy: 8, r: 3.2 }], P('M3 19.5c0-3.4 2.7-5.6 6-5.6s6 2.2 6 5.6'), ['circle', { cx: 17, cy: 9, r: 2.5 }], P('M16.8 13.9c2.6.2 4.4 2 4.4 4.8')],
  person: [['circle', { cx: 12, cy: 8, r: 3.8 }], P('M4.5 20.5c.8-3.8 3.8-6 7.5-6s6.7 2.2 7.5 6')],
  phone: [P('M6.6 3.5h2.7l1.4 4.2-2 1.4a12 12 0 0 0 6.2 6.2l1.4-2 4.2 1.4v2.7a2 2 0 0 1-2.2 2A17 17 0 0 1 4.6 5.7a2 2 0 0 1 2-2.2z')],
  medical: [P('M9.2 3.5h5.6v5.7h5.7v5.6h-5.7v5.7H9.2v-5.7H3.5V9.2h5.7z')],
  hand: [P('M8 12.5V6a1.5 1.5 0 0 1 3 0v5'), P('M11 10.5V4.5a1.5 1.5 0 0 1 3 0v6'), P('M14 10.5V6a1.5 1.5 0 0 1 3 0v7.5c0 4.3-2.6 7-6.3 7-2.6 0-4-1.2-5.4-3.4L3.6 13.3a1.5 1.5 0 0 1 2.4-1.7L8 14')],
  bell: [P('M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2H4.5z'), P('M10 20.5a2.2 2.2 0 0 0 4 0')],
  map: [P('M3.5 6.2 9 3.8l6 2.4 5.5-2.4v14l-5.5 2.4-6-2.4-5.5 2.4z'), P('M9 3.8v14M15 6.2v14')],
  route: [['circle', { cx: 6, cy: 18, r: 2.3 }], ['circle', { cx: 18, cy: 6, r: 2.3 }], P('M8.3 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.7')],
  pin: [P('M12 21.2s6.5-5.8 6.5-11.2a6.5 6.5 0 0 0-13 0c0 5.4 6.5 11.2 6.5 11.2z'), ['circle', { cx: 12, cy: 10, r: 2.4 }]],
  locate: [['circle', { cx: 12, cy: 12, r: 6.5 }], ['circle', { cx: 12, cy: 12, r: 2, fill: 1 }], P('M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3')],
  plus: [P('M12 5v14M5 12h14')],
  minus: [P('M5 12h14')],
  bulb: [P('M9 17.5h6M10 21h4'), P('M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.3 1 2.1v.6h5.2v-.6c0-.8.4-1.6 1-2.1A6 6 0 0 0 12 3z')],
  crossing: [P('M4 20 12 4l8 16z'), P('M12 10v4.5'), ['circle', { cx: 12, cy: 17.2, r: .9, fill: 1 }]],
  moon: [P('M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z')],
  flag: [P('M5 21V4'), P('M5 4.5h11.5l-2 4 2 4H5')],
  clock: [['circle', { cx: 12, cy: 12, r: 9 }], P('M12 7v5l3.2 2')],
  info: [['circle', { cx: 12, cy: 12, r: 9 }], P('M12 11v5.5'), ['circle', { cx: 12, cy: 7.8, r: 1, fill: 1 }]],
  lock: [['rect', { x: 5, y: 10.5, width: 14, height: 10, rx: 2 }], P('M8 10.5V7.5a4 4 0 0 1 8 0v3')],
  iphone: [['rect', { x: 6.5, y: 2.5, width: 11, height: 19, rx: 2.5 }], P('M10.5 5h3')],
  sos: [P('M12 3.2 2.6 19.5h18.8z'), P('M12 9.5v4.6'), ['circle', { cx: 12, cy: 16.9, r: 1.2, fill: 1 }]],
  arrow: [P('M15 5l-7 7 7 7')],
  layers: [P('M12 3.5 2.8 8.3 12 13l9.2-4.7z'), P('m2.8 12.4 9.2 4.7 9.2-4.7'), P('m2.8 16.4 9.2 4.7 9.2-4.7')],
  mic: [['rect', { x: 9, y: 3, width: 6, height: 11, rx: 3 }], P('M5.5 11a6.5 6.5 0 0 0 13 0'), P('M12 17.5V21M9 21h6')],
};

/** <svg> for `name` (aria-hidden: always paired with visible text or an aria-label on the control). */
export function icon(name, cls) {
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('aria-hidden', 'true');
  s.setAttribute('focusable', 'false');
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '1.9');
  s.setAttribute('stroke-linecap', 'round');
  s.setAttribute('stroke-linejoin', 'round');
  if (cls) s.setAttribute('class', cls);
  for (const [tag, attrs] of ICONS[name] || ICONS.info) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'fill') { el.setAttribute('fill', 'currentColor'); el.setAttribute('stroke', 'none'); } else el.setAttribute(k, String(v));
    }
    s.append(el);
  }
  return s;
}

export const ICON_NAMES = Object.keys(ICONS);
