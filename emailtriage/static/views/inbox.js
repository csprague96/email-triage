// Inbox: everything triaged, grouped by priority, with calm filters.
import { $, $$, api, bus, debounce, esc, icon, on, PRIORITIES, rowItem, store, toast } from '../ui.js';
import { openDetail } from '../detail.js';
import { checkInbox } from '../app.js';

let root = null;
let filters = { priority: '', tag: '', q: '', review: false, handled: false, hours: '168' };
let emails = [];
let activeIdx = -1;

export async function render(el, route) {
  root = el;
  activeIdx = -1;
  filters = { ...filters, priority: route.params.priority || '', tag: route.params.tag || '' };
  root.innerHTML = toolbar();
  wire();
  await load();
  const offs = [bus.on('emails-changed', load), bus.on('key', onKey)];
  return () => { offs.forEach((f) => f()); root = null; };
}

function toolbar() {
  const tags = store.tags?.tags || [];
  return `
    <div class="toolbar">
      <div class="search">${icon('search')}<input type="search" id="q" placeholder="Search sender, subject or text" value="${esc(filters.q)}" aria-label="Search"></div>
      <select id="tag" aria-label="Filter by tag"><option value="">All tags</option>${tags.map((t) => `<option ${t.name === filters.tag ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select>
      <select id="hours" aria-label="Time range">
        <option value="24" ${filters.hours === '24' ? 'selected' : ''}>Last 24 hours</option>
        <option value="72" ${filters.hours === '72' ? 'selected' : ''}>Last 3 days</option>
        <option value="168" ${filters.hours === '168' ? 'selected' : ''}>Last 7 days</option>
        <option value="720" ${filters.hours === '720' ? 'selected' : ''}>Last 30 days</option>
        <option value="" ${filters.hours === '' ? 'selected' : ''}>Everything</option>
      </select>
    </div>
    <div class="toolbar">
      <div class="chips" id="pchips">
        <button class="chip ${!filters.priority ? 'on' : ''}" data-pf="">All</button>
        ${PRIORITIES.map((p) => `<button class="chip ${filters.priority === p ? 'on' : ''}" data-pf="${p}"><span class="dot" data-p="${p}" style="background:var(--p-color)"></span>${p}<span class="n" data-count="${p}"></span></button>`).join('')}
      </div>
      <span style="flex:1"></span>
      <label class="switch small"><input type="checkbox" id="review" ${filters.review ? 'checked' : ''}><span class="track"></span>Unsure only</label>
      <label class="switch small"><input type="checkbox" id="handled" ${filters.handled ? 'checked' : ''}><span class="track"></span>Show handled</label>
    </div>
    <div id="list"></div>`;
}

function wire() {
  $('#q', root).addEventListener('input', debounce((e) => { filters.q = e.target.value.trim(); load(); }, 250));
  $('#tag', root).onchange = (e) => { filters.tag = e.target.value; load(); };
  $('#hours', root).onchange = (e) => { filters.hours = e.target.value; load(); };
  $('#review', root).onchange = (e) => { filters.review = e.target.checked; load(); };
  $('#handled', root).onchange = (e) => { filters.handled = e.target.checked; load(); };
  on(root, 'click', '[data-pf]', (_, b) => { filters.priority = b.dataset.pf; $$('[data-pf]', root).forEach((x) => x.classList.toggle('on', x === b)); load(); });
  on(root, 'click', '.rowitem', (_, r) => openDetail(r.dataset.id));
  on(root, 'keydown', '.rowitem', (ev, r) => { if (ev.key === 'Enter') openDetail(r.dataset.id); });
  on(root, 'click', '[data-run]', (_, b) => checkInbox(+b.dataset.run));
}

async function load() {
  if (!root) return;
  const list = $('#list', root);
  try {
    emails = await api('/emails', { query: { priority: filters.priority, tag: filters.tag, q: filters.q, review: filters.review || undefined, handled: filters.handled ? undefined : false, hours: filters.hours, limit: 400 } });
  } catch (e) { list.innerHTML = `<div class="empty"><h3>Could not load</h3><p>${esc(e.message)}</p></div>`; return; }

  // counts per priority for the chips (within the current search/tag/time filters)
  if (!filters.priority) {
    const counts = {};
    emails.forEach((e) => { counts[e.priority] = (counts[e.priority] || 0) + 1; });
    $$('[data-count]', root).forEach((el) => { el.textContent = counts[el.dataset.count] ? ` ${counts[el.dataset.count]}` : ''; });
  }

  if (!emails.length) {
    list.innerHTML = `<div class="empty"><div class="big">☁</div><h3>Nothing here</h3><p>${store.status?.total ? 'No triaged mail matches these filters.' : 'Nothing has been triaged yet.'}</p>
      ${store.status?.total ? '' : '<div class="row"><button class="btn primary" data-run="24">Triage the last 24 hours</button><button class="btn" data-run="168">Triage the last 7 days</button></div>'}</div>`;
    return;
  }
  if (filters.priority || filters.q) {
    list.innerHTML = `<div class="list">${emails.map((e) => rowItem(e)).join('')}</div>`;
  } else {
    const groups = PRIORITIES.map((p) => ({ p, items: emails.filter((e) => e.priority === p) })).filter((g) => g.items.length);
    list.innerHTML = groups.map((g, i) => `
      <details class="group" ${g.p === 'junk' || g.p === 'low' ? '' : 'open'}>
        <summary>${icon('chev', 'chev')}<span class="pill" data-p="${g.p}">${g.p}</span><span class="n">${g.items.length}</span>${g.p === 'low' ? '<span class="n">FYI, no reply expected</span>' : g.p === 'junk' ? '<span class="n">newsletters, marketing, cold outreach</span>' : ''}</summary>
        <div class="list">${g.items.map((e) => rowItem(e)).join('')}</div>
      </details>`).join('');
  }
  activeIdx = -1;
}

function onKey(e) {
  const rows = $$('.rowitem', root).filter((r) => r.offsetParent !== null);
  if (!rows.length) return;
  if (e.key === 'j' || e.key === 'k') {
    e.preventDefault();
    activeIdx = Math.max(0, Math.min(rows.length - 1, activeIdx + (e.key === 'j' ? 1 : -1)));
    rows.forEach((r, i) => r.classList.toggle('active', i === activeIdx));
    rows[activeIdx].scrollIntoView({ block: 'nearest' });
    return;
  }
  const row = rows[activeIdx];
  if (!row) return;
  if (e.key === 'Enter') openDetail(row.dataset.id);
  if (e.key === 'o') api(`/emails/${encodeURIComponent(row.dataset.id)}/open`, { method: 'POST' }).catch((er) => toast(er.message, { kind: 'error' }));
  if (e.key === 'h') api(`/emails/${encodeURIComponent(row.dataset.id)}/handled`, { method: 'POST', body: { handled: true } }).then(() => { toast('Handled', { kind: 'ok' }); load(); }).catch((er) => toast(er.message, { kind: 'error' }));
}
