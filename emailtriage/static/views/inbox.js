// Inbox: everything triaged, grouped by priority, with one row of filters.
import { $, $$, api, bus, cap, debounce, esc, icon, on, PRIORITIES, rowItem, store, toast } from '../ui.js';
import { openDetail } from '../detail.js';
import { checkInbox } from '../app.js';

let root = null;
const DEFAULTS = { priority: '', tag: '', q: '', show: 'open', hours: '168' };
let filters = { ...DEFAULTS };
let emails = [];
let activeIdx = -1;

export async function render(el, route) {
  root = el;
  activeIdx = -1;
  filters = { ...filters, priority: route.params.priority || '', tag: route.params.tag || '', show: route.params.review ? 'unsure' : filters.show };
  root.innerHTML = toolbar();
  wire();
  await load();
  const offs = [bus.on('emails-changed', load), bus.on('key', onKey)];
  return () => { offs.forEach((f) => f()); root = null; };
}

function toolbar() {
  const tags = store.tags?.tags || [];
  const opt = (v, l, cur) => `<option value="${v}" ${cur === v ? 'selected' : ''}>${l}</option>`;
  return `
    <div class="toolbar">
      <div class="search">${icon('search')}<input type="search" id="q" placeholder="Search sender, subject or text" value="${esc(filters.q)}" aria-label="Search"></div>
      <select id="show" aria-label="Show">${opt('open', 'Not done', filters.show)}${opt('unsure', 'Unsure only', filters.show)}${opt('all', 'Include done', filters.show)}</select>
      <select id="tag" aria-label="Filter by tag"><option value="">All tags</option>${tags.map((t) => `<option ${t.name === filters.tag ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select>
      <select id="hours" aria-label="Time range">${opt('24', 'Last 24 hours', filters.hours)}${opt('72', 'Last 3 days', filters.hours)}${opt('168', 'Last 7 days', filters.hours)}${opt('720', 'Last 30 days', filters.hours)}${opt('', 'All time', filters.hours)}</select>
    </div>
    <div class="toolbar chips" id="pchips">
      <button class="chip ${!filters.priority ? 'on' : ''}" data-pf="">All</button>
      ${PRIORITIES.map((p) => `<button class="chip ${filters.priority === p ? 'on' : ''}" data-pf="${p}">${cap(p)}<span class="n" data-count="${p}"></span></button>`).join('')}
    </div>
    <div id="list"></div>`;
}

function wire() {
  $('#q', root).addEventListener('input', debounce((e) => { filters.q = e.target.value.trim(); load(); }, 250));
  $('#tag', root).onchange = (e) => { filters.tag = e.target.value; load(); };
  $('#hours', root).onchange = (e) => { filters.hours = e.target.value; load(); };
  $('#show', root).onchange = (e) => { filters.show = e.target.value; load(); };
  on(root, 'click', '[data-pf]', (_, b) => { filters.priority = b.dataset.pf; $$('[data-pf]', root).forEach((x) => x.classList.toggle('on', x === b)); load(); });
  on(root, 'click', '.rowitem', (_, r) => openDetail(r.dataset.id));
  on(root, 'keydown', '.rowitem', (ev, r) => { if (ev.key === 'Enter') openDetail(r.dataset.id); });
  on(root, 'click', '[data-run]', (_, b) => checkInbox(+b.dataset.run));
  on(root, 'click', '[data-clear]', () => { filters = { ...DEFAULTS }; root.innerHTML = toolbar(); wire(); load(); });
}

async function load() {
  if (!root) return;
  const list = $('#list', root);
  try {
    emails = await api('/emails', { query: { priority: filters.priority, tag: filters.tag, q: filters.q, review: filters.show === 'unsure' || undefined, handled: filters.show === 'all' ? undefined : false, hours: filters.hours, limit: 400 } });
  } catch (e) { list.innerHTML = `<div class="empty"><h3>Could not load the inbox</h3><p>${esc(e.message)}</p></div>`; return; }

  // counts per priority for the chips (within the current search/tag/time filters)
  if (!filters.priority) {
    const counts = {};
    emails.forEach((e) => { counts[e.priority] = (counts[e.priority] || 0) + 1; });
    $$('[data-count]', root).forEach((el) => { el.textContent = counts[el.dataset.count] || ''; });
  }

  if (!emails.length) {
    list.innerHTML = store.status?.total
      ? '<div class="empty"><h3>No emails match these filters</h3><button class="btn" data-clear>Clear filters</button></div>'
      : '<div class="empty"><h3>Nothing triaged yet</h3><div class="row"><button class="btn primary" data-run="24">Triage the last 24 hours</button><button class="btn" data-run="168">Triage the last 7 days</button></div></div>';
    return;
  }
  if (filters.priority || filters.q) {
    list.innerHTML = `<div class="list">${emails.map((e) => rowItem(e)).join('')}</div>`;
  } else {
    const groups = PRIORITIES.map((p) => ({ p, items: emails.filter((e) => e.priority === p) })).filter((g) => g.items.length);
    const note = { low: 'No reply expected', junk: 'Newsletters and marketing' };
    list.innerHTML = groups.map((g) => `
      <details class="group" ${g.p === 'junk' || g.p === 'low' ? '' : 'open'}>
        <summary>${icon('chev', 'chev')}<span class="pill" data-p="${g.p}">${cap(g.p)}</span><span class="n">${g.items.length}${note[g.p] ? ` · ${note[g.p]}` : ''}</span></summary>
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
  if (e.key === 'h') api(`/emails/${encodeURIComponent(row.dataset.id)}/handled`, { method: 'POST', body: { handled: true } }).then(() => { toast('Marked done'); load(); }).catch((er) => toast(er.message, { kind: 'error' }));
}
