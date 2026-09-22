// Today: the one screen to look at. What needs you, in order, one decision per card.
import { $, $$, api, bus, esc, firstName, fmtTime, icon, on, pill, rowItem, snippet, store, tagChip, toast, whyList } from '../ui.js';
import { openDetail } from '../detail.js';
import { renderDraft, wireDraftButtons } from '../detail.js';
import { checkInbox } from '../app.js';

let data = null;
let activeIdx = -1;
let root = null;

export async function render(el) {
  root = el;
  activeIdx = -1;
  root.innerHTML = '<p class="muted">Loading…</p>';
  await load();
  const offs = [
    bus.on('emails-changed', load),
    bus.on('drafts-changed', load),
    bus.on('key', onKey),
  ];
  const timer = setInterval(load, 60000);
  wire();
  return () => { offs.forEach((f) => f()); clearInterval(timer); root = null; };
}

async function load() {
  if (!root) return;
  try { data = await api('/today'); } catch (e) { root.innerHTML = `<div class="empty"><h3>Could not load</h3><p>${esc(e.message)}</p></div>`; return; }
  bus.emit('today-counts', { needs_you: data.needs_you.length });
  draw();
}

function greeting() {
  const h = new Date().getHours();
  const name = firstName(store.status?.owner_name);
  const part = h < 5 ? 'Late night' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  return name ? `${part}, ${name}` : part;
}

function draw() {
  if (!root || !data) return;
  const s = store.status || {};
  const n = data.needs_you.length;
  const c = data.counts_24h || {};
  const total24 = Object.values(c).reduce((a, b) => a + b, 0);
  const handled = data.handled_today || 0;
  const denom = handled + n;
  const pct = denom ? Math.round((handled / denom) * 100) : 100;
  const date = new Date().toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });

  if (!s.total && !total24) {
    root.innerHTML = `<div class="hero"><div><div class="greet">${esc(greeting())} · ${esc(date)}</div><h1>Let's set up your inbox</h1></div></div>
      <div class="card pad empty" style="text-align:left;justify-items:start">
        <h3>Nothing has been triaged yet</h3>
        <p class="muted">Pick a starting window. Jev reads each email once and sorts it into junk, low, medium, high or urgent, then tags it. Roughly a third of a second and a hundredth of a cent per email.</p>
        <div class="row"><button class="btn primary" data-run="24">Triage the last 24 hours</button><button class="btn" data-run="168">Triage the last 7 days</button></div>
      </div>`;
    return;
  }

  const cards = data.needs_you.map((e, i) => focusCard(e, i === activeIdx)).join('');
  root.innerHTML = `
    <div class="hero">
      <div>
        <div class="greet">${esc(greeting())} · ${esc(date)}</div>
        <h1>${n ? `<b>${n}</b> ${n === 1 ? 'thing needs' : 'things need'} you` : 'Nothing needs you right now'}</h1>
        ${denom ? `<div class="progress" style="width:260px" title="${handled} handled today"><i style="width:${pct}%"></i></div><div class="muted small" style="margin-top:6px">${handled} handled today${n ? `, ${n} to go` : ''}</div>` : ''}
      </div>
      <div class="stats">
        <div class="stat"><b>${total24}</b><span>last 24h</span></div>
        <div class="stat" data-p="urgent"><b style="color:var(--p-color)">${c.urgent || 0}</b><span>urgent</span></div>
        <div class="stat" data-p="high"><b style="color:var(--p-color)">${c.high || 0}</b><span>high</span></div>
        <div class="stat"><b>${data.drafts.length}</b><span>drafts</span></div>
      </div>
    </div>

    ${n ? `<div class="focus-list" id="focus">${cards}</div>` : `<div class="calm"><h2>Inbox is quiet. Nice.</h2><p class="muted" style="margin-top:6px">Urgent and high-priority mail from the last ${Math.round(data.hours / 24)} days is all handled or replied to. New mail is checked every ${Math.round((s.poll_seconds || 120) / 60)} minutes.</p></div>`}

    ${data.drafts.length ? `<section class="section">
      <div class="section-head"><h2>Drafts waiting in Outlook</h2><span class="muted">${data.drafts.length} ready to read, edit and send</span><span class="spacer"></span><a href="#/drafts" class="btn ghost sm">All drafts ${icon('chev')}</a></div>
      <div id="draftList">${data.drafts.slice(0, 3).map((d) => draftCard(d)).join('')}</div>
    </section>` : ''}

    ${data.review.length ? `<section class="section">
      <div class="section-head"><h2>Worth a look</h2><span class="muted">Jev was not sure about these. Correcting them teaches it.</span></div>
      <div class="list">${data.review.map((e) => rowItem(e)).join('')}</div>
    </section>` : ''}

    ${data.later_total ? `<section class="section">
      <details class="group"><summary>${icon('chev', 'chev')}<span>Later</span><span class="n">${data.later_total} medium-priority, no rush</span></summary>
        <div class="list">${data.later.map((e) => rowItem(e)).join('')}</div>
        ${data.later_total > data.later.length ? `<div style="margin-top:10px"><a class="btn ghost sm" href="#/inbox?priority=medium">See all ${data.later_total} ${icon('chev')}</a></div>` : ''}
      </details>
    </section>` : ''}

    <p class="muted small" style="margin-top:36px">Low and junk mail is out of the way in <a href="#/inbox">Inbox</a>. Press <kbd>?</kbd> for shortcuts.</p>`;
}

function focusCard(e, active) {
  const why = whyList(e).slice(0, 3);
  return `<article class="focus-card ${active ? 'active' : ''}" data-id="${esc(e.id)}" data-p="${esc(e.priority)}">
    <div class="top">${pill(e.priority)}<span class="from">${esc(e.sender_name || e.sender_email)}</span>${(e.tags || []).slice(0, 3).map((t) => tagChip(t, true)).join('')}<span class="time">${esc(fmtTime(e.received))}</span></div>
    <div class="subject" data-act="detail">${esc(e.subject || '(no subject)')}</div>
    ${why.length ? `<div class="why">${why.map((w, i) => i === 0 ? `<b>${esc(w)}</b>` : esc(w)).join(' · ')}</div>` : ''}
    <div class="snippet">${esc(snippet(e.body_text, 220))}</div>
    <div class="actions">
      <button class="btn primary sm" data-act="handled">${icon('check')}Handled</button>
      <button class="btn sm" data-act="open">${icon('open')}Open in Outlook</button>
      ${store.status?.drafting_enabled && !e.ready_drafts ? `<button class="btn sm" data-act="draft">${icon('pen')}Draft reply</button>` : ''}
      ${e.ready_drafts ? `<span class="draftflag">${icon('check')} Draft ready in Outlook</span>` : ''}
      <span class="spacer"></span>
      <button class="btn ghost sm" data-act="detail">Details ${icon('chev')}</button>
    </div>
  </article>`;
}

function draftCard(d) {
  return `<div class="draft ${d.passed ? '' : 'failed'}" data-draft="${d.id}">
    <div class="meta">${pill(d.priority || 'medium')}<b>${esc(d.subject)}</b><span>to ${esc(d.sender_name || d.sender_email)}</span><span>${esc(fmtTime(d.created_at))}</span></div>
    <pre>${esc(snippet(d.body_text, 320))}</pre>
    <div class="row">
      ${d.outlook_draft_id ? `<button class="btn sm primary" data-dact="open">${icon('open')}Open in Outlook</button>` : ''}
      <button class="btn sm ok" data-dact="done">${icon('check')}Done</button>
      <button class="btn sm ghost" data-view="${esc(d.email_id)}">View email</button>
    </div></div>`;
}

function wire() {
  on(root, 'click', '[data-run]', (_, b) => checkInbox(+b.dataset.run));
  on(root, 'click', '.focus-card [data-act]', async (ev, b) => {
    ev.stopPropagation();
    const card = b.closest('.focus-card');
    await cardAction(card, b.dataset.act);
  });
  on(root, 'click', '.rowitem', (_, r) => openDetail(r.dataset.id));
  on(root, 'keydown', '.rowitem', (ev, r) => { if (ev.key === 'Enter') openDetail(r.dataset.id); });
  on(root, 'click', '[data-view]', (_, b) => openDetail(b.dataset.view));
  wireDraftButtons(root, () => load());
}

async function cardAction(card, act) {
  const id = card.dataset.id;
  try {
    if (act === 'detail') openDetail(id);
    else if (act === 'open') await api(`/emails/${encodeURIComponent(id)}/open`, { method: 'POST' });
    else if (act === 'draft') { openDetail(id); setTimeout(() => $('#sheet [data-act=draft]')?.click(), 400); }
    else if (act === 'handled') {
      card.classList.add('leaving');
      await api(`/emails/${encodeURIComponent(id)}/handled`, { method: 'POST', body: { handled: true } });
      const t = toast('Handled. Undo?', { ms: 6000 });
      t.style.cursor = 'pointer';
      t.onclick = async () => { t.remove(); await api(`/emails/${encodeURIComponent(id)}/handled`, { method: 'POST', body: { handled: false } }); load(); };
      setTimeout(load, 180);
    }
  } catch (e) { card.classList.remove('leaving'); toast(e.message, { kind: 'error', ms: 5000 }); }
}

function onKey(e) {
  const cards = $$('.focus-card', root);
  if (!cards.length) return;
  if (e.key === 'j' || e.key === 'k') {
    e.preventDefault();
    activeIdx = Math.max(0, Math.min(cards.length - 1, activeIdx + (e.key === 'j' ? 1 : -1)));
    cards.forEach((c, i) => c.classList.toggle('active', i === activeIdx));
    cards[activeIdx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    return;
  }
  const card = cards[activeIdx];
  if (!card) return;
  if (e.key === 'Enter') cardAction(card, 'detail');
  if (e.key === 'h') { cardAction(card, 'handled'); activeIdx = Math.min(activeIdx, cards.length - 2); }
  if (e.key === 'o') cardAction(card, 'open');
}
