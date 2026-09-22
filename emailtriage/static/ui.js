// Shared helpers: DOM, formatting, API, toasts, icons, and the small cross-view store.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const PRIORITIES = ['urgent', 'high', 'medium', 'low', 'junk'];

// ---- store + event bus ---------------------------------------------------------
export const store = {
  status: null,
  tags: null,
  auth: null,
  tagColor(name) {
    const t = (this.tags?.tags || []).find((x) => x.name === name);
    return t?.color || 'var(--muted)';
  },
};

const listeners = new Map();
export const bus = {
  on(evt, fn) {
    if (!listeners.has(evt)) listeners.set(evt, new Set());
    listeners.get(evt).add(fn);
    return () => listeners.get(evt)?.delete(fn);
  },
  emit(evt, payload) {
    listeners.get(evt)?.forEach((fn) => { try { fn(payload); } catch (e) { console.error(e); } });
  },
};

// ---- API -------------------------------------------------------------------------
export async function api(path, { method = 'GET', body, query } = {}) {
  let url = '/api' + path;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') qs.set(k, v);
    const s = qs.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  const r = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'EmailTriage' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText || `HTTP ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

// ---- toasts ---------------------------------------------------------------------
// action: { label, onClick } adds one button to the toast, e.g. Undo.
export function toast(msg, { kind = '', ms = 3200, action } = {}) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  const text = document.createElement('span');
  text.textContent = msg;
  el.appendChild(text);
  if (action) {
    const b = document.createElement('button');
    b.textContent = action.label;
    b.onclick = () => { el.remove(); action.onClick(); };
    el.appendChild(b);
  }
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
  return el;
}

// ---- formatting ---------------------------------------------------------------
const DAY = 86400000;
export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return `${Math.round(diff / 60000)} min ago`;
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return time;
  const yesterday = new Date(now - DAY);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`;
  if (diff < 6 * DAY) return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + time;
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}
export function fmtDateTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
export function initials(name, email) {
  const src = (name || email || '?').trim();
  const parts = src.replace(/[<>"]/g, '').split(/[\s._@-]+/).filter(Boolean);
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?';
}
export function snippet(text, n = 160) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t;
}
export function firstName(name) {
  return (name || '').split(/\s+/)[0] || '';
}
export const cap = (s) => { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); };
export const debounce = (fn, ms = 250) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

// Plain-language reasons an email landed where it did, built from Jev's signals. No numbers unless useful.
export function whyList(e) {
  const s = e.signals || {};
  const out = [];
  if (s.escalation >= 0.6) out.push('Sounds blocked or frustrated');
  if (s.action_requested >= 0.6) out.push('Asks you to do something');
  else if (s.needs_reply >= 0.6) out.push('Waiting on your reply');
  const ts = e.time_sensitivity || 0;
  if (ts >= 2.5) out.push('Needs handling now');
  else if (ts >= 1.5) out.push('Today or tomorrow');
  else if (ts >= 0.8) out.push('Sometime this week');
  if (s.external_business >= 0.6) out.push('External partner or customer');
  if (s.recipient_only_copied >= 0.7) out.push('You are only copied');
  if (s.automated >= 0.7) out.push('Automated notification');
  if (e.user_priority) out.push('You set this priority');
  else if (e.needs_review) out.push(`Jev was unsure (${Math.round((e.confidence || 0) * 100)}% confident)`);
  return out;
}
export function whyLine(e, max = 3) {
  return whyList(e).slice(0, max).join(' · ');
}

// ---- small components ------------------------------------------------------------
export const pill = (p) => `<span class="pill" data-p="${esc(p)}">${esc(cap(p))}</span>`;
export const tagChip = (name) => `<span class="tag" style="--tag-color:${esc(store.tagColor(name))}"><i></i>${esc(name)}</span>`;
export const avatar = (name, email) => `<span class="avatar" aria-hidden="true">${esc(initials(name, email))}</span>`;

// One line per email: who, what, when. Read state is shown by weight, not an extra marker.
export function rowItem(e, { showHandled = true } = {}) {
  const flags = [];
  if (e.ready_drafts) flags.push('<span class="badge ok">Draft ready</span>');
  if (e.needs_review && !e.user_priority) flags.push('<span class="badge warn">Unsure</span>');
  if (showHandled && e.handled_at) flags.push('<span class="badge">Done</span>');
  const from = e.sender_name || e.sender_email;
  return `<div class="rowitem ${e.handled_at ? 'handled' : ''}" data-id="${esc(e.id)}" data-p="${esc(e.priority)}" role="button" tabindex="0">
    <span class="pdot" title="${esc(cap(e.priority))}"></span>
    <span class="from ${e.is_read ? '' : 'unread'}" title="${esc(e.sender_email)}">${esc(from)}</span>
    <span class="mid"><span class="subj" data-from="${esc(from)}">${esc(e.subject || '(no subject)')}</span><span class="snip">${esc(snippet(e.body_text, 120))}</span></span>
    <span class="right">${flags.join('')}<span>${esc(fmtTime(e.received))}</span></span>
  </div>`;
}

// Event delegation: on(root, 'click', '[data-act]', (ev, el) => ...)
export function on(root, type, selector, handler) {
  root.addEventListener(type, (ev) => {
    const el = ev.target.closest(selector);
    if (el && root.contains(el)) handler(ev, el);
  });
}

export function copyText(text) {
  return navigator.clipboard.writeText(text).then(() => toast('Copied'), () => toast('Could not copy', { kind: 'error' }));
}

// ---- icons (inline SVG, currentColor) -------------------------------------------
const I = {
  today: '<path d="M8 2v3M16 2v3M3.5 9.5h17M5 5h14a1.5 1.5 0 0 1 1.5 1.5v13A1.5 1.5 0 0 1 19 21H5a1.5 1.5 0 0 1-1.5-1.5v-13A1.5 1.5 0 0 1 5 5z"/><path d="M8 14l2.5 2.5L16 11"/>',
  inbox: '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 6h14l2 7v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6l2-7z"/>',
  drafts: '<path d="M14 3l7 7-11 11H3v-7L14 3z"/><path d="M12 5l7 7"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  open: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"/><path d="M19 17l.8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8L19 17z"/>',
  slack: '<path d="M9 3a2 2 0 0 0 0 4h2V5a2 2 0 0 0-2-2zM15 21a2 2 0 0 0 0-4h-2v2a2 2 0 0 0 2 2zM3 15a2 2 0 0 0 4 0v-2H5a2 2 0 0 0-2 2zM21 9a2 2 0 0 0-4 0v2h2a2 2 0 0 0 2-2z"/><path d="M9 9h6v6H9z"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
};
export function icon(name, cls = '') {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[name] || ''}</svg>`;
}
