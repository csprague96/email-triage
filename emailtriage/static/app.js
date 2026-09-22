// Boot, routing, theme, shell (sidebar + top bar), sign-in gate, keyboard shortcuts.
import { $, $$, api, bus, esc, firstName, icon, initials, on, store, toast } from './ui.js';
import { closeDetail, isDetailOpen, openDetail } from './detail.js';
import * as today from './views/today.js';
import * as inbox from './views/inbox.js';
import * as drafts from './views/drafts.js';
import * as settings from './views/settings.js';

const VIEWS = { today, inbox, drafts, settings };
const NAV = [
  { id: 'today', label: 'Today', icon: 'today' },
  { id: 'inbox', label: 'Inbox', icon: 'inbox' },
  { id: 'drafts', label: 'Drafts', icon: 'drafts' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];
let current = null; // { view, sub, params }
let dispose = null;

// ---- theme ---------------------------------------------------------------------
const media = matchMedia('(prefers-color-scheme: dark)');
export function themePref() { try { return localStorage.getItem('et-theme') || 'system'; } catch { return 'system'; } }
export function setTheme(pref) {
  try { localStorage.setItem('et-theme', pref); } catch { /* private mode */ }
  applyTheme();
  bus.emit('theme', pref);
}
function applyTheme() {
  const pref = themePref();
  const dark = pref === 'dark' || (pref === 'system' && media.matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.themePref = pref;
  renderTopbar();
}
media.addEventListener('change', applyTheme);

// ---- routing -------------------------------------------------------------------
function parseRoute() {
  const hash = location.hash.replace(/^#\/?/, '');
  const [pathPart, qs] = hash.split('?');
  const [view = 'today', sub = ''] = pathPart.split('/');
  const params = Object.fromEntries(new URLSearchParams(qs || ''));
  return { view: VIEWS[view] ? view : 'today', sub, params };
}
export function navigate(path) { location.hash = path.startsWith('#') ? path : '#/' + path.replace(/^\//, ''); }

async function route() {
  const r = parseRoute();
  current = r;
  if (typeof dispose === 'function') { try { dispose(); } catch { /* ignore */ } dispose = null; }
  renderSidebar();
  renderTopbar();
  const host = $('#view');
  const root = document.createElement('div');
  root.className = 'view-root';
  host.replaceChildren(root);
  const gated = r.view !== 'settings' && store.auth && !store.auth.signed_in;
  if (gated) { renderGate(root); return; }
  dispose = await VIEWS[r.view].render(root, r);
  host.focus({ preventScroll: true });
}
window.addEventListener('hashchange', route);

// ---- shell ----------------------------------------------------------------------
function renderSidebar() {
  const s = store.status || {};
  const t = store.todayCounts || {};
  const items = NAV.map((n) => {
    let count = '';
    if (n.id === 'today' && t.needs_you) count = `<span class="count hot">${t.needs_you}</span>`;
    if (n.id === 'drafts' && s.ready_drafts) count = `<span class="count">${s.ready_drafts}</span>`;
    return `<a href="#/${n.id}" class="${current?.view === n.id ? 'active' : ''}">${icon(n.icon)}<span>${n.label}</span>${count}</a>`;
  }).join('');
  const dot = (ok) => `<span class="dot ${ok ? '' : 'bad'}"></span>`;
  $('#sidebar').innerHTML = `
    <div class="brand"><span class="logo">${icon('mail')}</span><span>Email Triage</span></div>
    <nav class="nav">${items}</nav>
    <div class="foot">
      <div class="statusline">${dot(s.backend_ok)}<span>${s.backend_ok ? 'Mailbox connected' : 'Mailbox offline'}</span></div>
      <div class="statusline">${dot(s.jev_configured)}<span>Jev ${s.jev_configured ? 'ready' : 'not set up'}</span></div>
      <div class="statusline"><span class="dot ${s.watcher_alive ? '' : 'off'}"></span><span>${s.watcher_alive ? `Checking every ${Math.round((s.poll_seconds || 120) / 60)} min` : 'Watcher not running here'}</span></div>
      <div class="statusline" style="color:var(--muted)">v${esc(s.version || '')}</div>
    </div>`;
}

function renderTopbar() {
  const s = store.status || {};
  const title = { today: 'Today', inbox: 'Inbox', drafts: 'Drafts', settings: 'Settings' }[current?.view || 'today'];
  const pref = themePref();
  const themeIcon = pref === 'system' ? 'monitor' : pref === 'dark' ? 'moon' : 'sun';
  const name = s.owner_name || store.auth?.name || store.auth?.account || '';
  $('#topbar').innerHTML = `
    <span class="title">${title}</span>
    <span class="spacer"></span>
    <button class="btn ghost sm" data-act="check" title="Check the inbox now">${icon('refresh')}<span class="hide-sm">Check inbox</span></button>
    <button class="btn ghost icon" data-act="theme" title="Theme: ${pref}. Click to change." aria-label="Change theme">${icon(themeIcon)}</button>
    <button class="btn ghost icon" data-act="help" title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">${icon('keyboard')}</button>
    <div class="account">
      <button class="btn ghost sm" data-act="account" aria-haspopup="menu"><span class="avatar" style="width:28px;height:28px;font-size:.72rem">${esc(initials(name, s.owner_email))}</span><span class="hide-sm">${esc(firstName(name) || 'Account')}</span></button>
    </div>`;
}
on($('#topbar'), 'click', '[data-act]', async (ev, el) => {
  const act = el.dataset.act;
  if (act === 'theme') {
    const next = { system: 'light', light: 'dark', dark: 'system' }[themePref()];
    setTheme(next);
    toast(`Theme: ${next}`);
  } else if (act === 'check') {
    await checkInbox();
  } else if (act === 'help') {
    toggleHelp();
  } else if (act === 'account') {
    toggleAccountMenu(el.parentElement);
  }
});

function toggleAccountMenu(host) {
  const existing = $('.menu', host);
  if (existing) { existing.remove(); return; }
  const s = store.status || {};
  const a = store.auth || {};
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.setAttribute('role', 'menu');
  menu.innerHTML = `
    <div class="who"><b>${esc(s.owner_name || a.name || 'Not connected')}</b><span class="muted">${esc(s.owner_email || a.account || '')}</span></div>
    <button data-m="settings">Settings</button>
    ${a.backend === 'graph' ? '<button data-m="signout">Sign out of Microsoft 365</button>' : '<button data-m="reconnect">Reconnect Outlook</button>'}`;
  host.appendChild(menu);
  const close = (e) => { if (!host.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); } };
  setTimeout(() => document.addEventListener('click', close));
  on(menu, 'click', '[data-m]', async (_, b) => {
    menu.remove();
    if (b.dataset.m === 'settings') navigate('settings/account');
    if (b.dataset.m === 'signout') {
      try { store.auth = await api('/auth/signout', { method: 'POST' }); toast('Signed out'); await refreshStatus(); route(); } catch (e) { toast(e.message, { kind: 'error' }); }
    }
    if (b.dataset.m === 'reconnect') {
      try { store.auth = await api('/auth/connect', { method: 'POST' }); toast('Outlook connected', { kind: 'ok' }); await refreshStatus(); route(); } catch (e) { toast(e.message, { kind: 'error', ms: 6000 }); }
    }
  });
}

export async function checkInbox(hours) {
  const t = toast(hours ? `Triaging the last ${hours} hours. This can take a minute…` : 'Checking the inbox…', { ms: 60000 });
  try {
    const r = await api('/run', { method: 'POST', body: hours ? { hours, limit: 400 } : {} });
    t.remove();
    toast(r.note || r.reason || 'Done', { kind: 'ok' });
    await refreshStatus();
    bus.emit('emails-changed');
  } catch (e) { t.remove(); toast('Could not check: ' + e.message, { kind: 'error', ms: 6000 }); }
}

// ---- sign-in gate ---------------------------------------------------------------
function renderGate(root) {
  const a = store.auth || {};
  let body = '';
  if (a.backend === 'graph') {
    if (a.needs_client_id) {
      body = `<h1>Sign in with Microsoft</h1>
        <p class="muted">Microsoft 365 sign-in needs the client ID of your organisation's Entra app registration. Paste it once in Settings and come back here.</p>
        <div class="row"><a class="btn primary" href="#/settings/account">Open account settings</a></div>`;
    } else if (a.pending) {
      body = `<h1>Finish signing in</h1>
        <p class="muted">Open the Microsoft page and enter this code. This page updates by itself once you are done.</p>
        <div class="code" id="devcode">${esc(a.pending.user_code)}</div>
        <div class="row">
          <a class="btn primary" href="${esc(a.pending.verification_uri)}" target="_blank" rel="noopener">${icon('open')} Open microsoft.com/devicelogin</a>
          <button class="btn" data-act="copycode">${icon('copy')} Copy code</button>
        </div>
        <p class="muted small">Waiting for Microsoft…</p>`;
    } else {
      body = `<h1>Sign in with Microsoft</h1>
        <p class="muted">Email Triage reads your Microsoft 365 mailbox through the Graph API. You sign in once; the token stays on this PC. Nothing is ever sent or deleted on your behalf.</p>
        ${a.error ? `<div class="error">${esc(a.error)}</div>` : ''}
        <div class="row"><button class="btn primary" data-act="connect">Sign in with Microsoft</button><a class="btn ghost" href="#/settings/account">Use classic Outlook instead</a></div>`;
    }
  } else {
    body = `<h1>Connect Outlook</h1>
      <p class="muted">Email Triage reads the mailbox that classic Outlook on this PC is already signed in to. No password needed. It only adds categories and saves drafts; nothing is sent, moved or deleted.</p>
      ${a.error ? `<div class="error">${esc(a.error)}</div>` : ''}
      <div class="row"><button class="btn primary" data-act="connect">Connect to Outlook</button><a class="btn ghost" href="#/settings/account">Sign in with Microsoft 365 instead</a></div>
      <p class="muted small">Not working? Classic Outlook must be installed and open. The "new Outlook" app cannot be read this way; use Microsoft 365 sign-in for it.</p>`;
  }
  root.innerHTML = `<div class="gate"><div class="card">
    <div class="logo-big">${icon('mail')}</div>${body}</div></div>`;
  on(root, 'click', '[data-act]', async (_, el) => {
    if (el.dataset.act === 'copycode') { navigator.clipboard.writeText($('#devcode').textContent); toast('Code copied'); }
    if (el.dataset.act === 'connect') {
      el.disabled = true; el.textContent = 'Connecting…';
      try {
        store.auth = await api('/auth/connect', { method: 'POST' });
        if (store.auth.signed_in) { toast('Connected', { kind: 'ok' }); await refreshStatus(); }
        route();
      } catch (e) { store.auth = { ...store.auth, error: e.message }; route(); }
    }
  });
  if (a.pending) {
    const poll = setInterval(async () => {
      if (current?.view === 'settings' || !document.body.contains(root.firstChild)) { clearInterval(poll); return; }
      try {
        const st = await api('/auth');
        if (st.signed_in) { clearInterval(poll); store.auth = st; toast('Signed in', { kind: 'ok' }); await refreshStatus(); route(); }
        else if (!st.pending) { clearInterval(poll); store.auth = st; route(); }
      } catch { /* keep polling */ }
    }, 3000);
    dispose = () => clearInterval(poll);
  }
}

// ---- status polling -------------------------------------------------------------
export async function refreshStatus() {
  try {
    const [status, auth] = await Promise.all([api('/status'), api('/auth')]);
    store.status = status;
    store.auth = auth;
    if (!store.tags) store.tags = await api('/tags');
    renderSidebar();
    renderTopbar();
    bus.emit('status', status);
  } catch (e) {
    store.status = store.status || {};
    $('#sidebar .foot')?.insertAdjacentHTML('afterbegin', '<div class="statusline"><span class="dot bad"></span>Server unreachable</div>');
  }
}
bus.on('today-counts', (c) => { store.todayCounts = c; renderSidebar(); });
bus.on('tags-changed', async () => { store.tags = await api('/tags'); });

// ---- keyboard -------------------------------------------------------------------
function toggleHelp() {
  const h = $('#help');
  if (!h.classList.contains('hidden')) { h.classList.add('hidden'); return; }
  h.innerHTML = `<div class="card">
    <div class="row between"><h2>Keyboard shortcuts</h2><button class="btn ghost icon" data-close aria-label="Close">${icon('close')}</button></div>
    <div class="kbd-list">
      <span><kbd>j</kbd> <kbd>k</kbd></span><span>Move between emails</span>
      <kbd>Enter</kbd><span>Open the selected email</span>
      <kbd>h</kbd><span>Mark the selected email handled</span>
      <kbd>o</kbd><span>Open the selected email in Outlook</span>
      <kbd>Esc</kbd><span>Close the panel</span>
      <span><kbd>1</kbd> … <kbd>4</kbd></span><span>Today, Inbox, Drafts, Settings</span>
      <kbd>r</kbd><span>Check the inbox now</span>
      <kbd>t</kbd><span>Cycle the theme</span>
      <kbd>?</kbd><span>This list</span>
    </div></div>`;
  h.classList.remove('hidden');
  h.onclick = (e) => { if (e.target === h || e.target.closest('[data-close]')) h.classList.add('hidden'); };
}
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toLowerCase();
  const typing = ['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable;
  if (e.key === 'Escape') {
    if (!$('#help').classList.contains('hidden')) { $('#help').classList.add('hidden'); return; }
    if (isDetailOpen()) { closeDetail(); return; }
    if (typing) e.target.blur();
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '?') { toggleHelp(); return; }
  if (['1', '2', '3', '4'].includes(e.key)) { navigate(NAV[+e.key - 1].id); return; }
  if (e.key === 't') { setTheme({ system: 'light', light: 'dark', dark: 'system' }[themePref()]); return; }
  if (e.key === 'r') { checkInbox(); return; }
  bus.emit('key', e);
});

// ---- boot ---------------------------------------------------------------------
(async () => {
  applyTheme();
  renderSidebar();
  renderTopbar();
  await refreshStatus();
  await route();
  setInterval(refreshStatus, 30000);
  window.openDetail = openDetail; // handy from the console
})();
