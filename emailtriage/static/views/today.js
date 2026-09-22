// Today: the one screen to look at. What needs you, in order, one decision per card.
import { $, $$, api, bus, esc, fmtTime, icon, on, snippet, store, toast, whyList } from '../ui.js';
import { openDetail, wireDraftButtons } from '../detail.js';
import { checkInbox } from '../app.js';

let data = null;
let activeIdx = -1;
let root = null;

export async function render(el) {
  root = el;
  activeIdx = -1;
  root.innerHTML = '<p class="muted">Loading</p>';
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
  try { data = await api('/today'); } catch (e) { root.innerHTML = `<div class="empty"><h3>Could not load Today</h3><p>${esc(e.message)}</p></div>`; return; }
  bus.emit('today-counts', { needs_you: data.needs_you.length });
  draw();
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

  if (!s.total && !total24) {
    root.innerHTML = `<div class="page-head"><h1>Set up triage</h1></div>
      <div class="card pad empty left">
        <p>Choose how far back to start. Jev sorts each email by priority and tags it.</p>
        <div class="row"><button class="btn primary" data-run="24">Last 24 hours</button><button class="btn" data-run="168">Last 7 days</button></div>
      </div>`;
    return;
  }

  const cards = data.needs_you.map((e, i) => focusCard(e, i === activeIdx)).join('');
  const later = [];
  if (data.review.length) later.push(linkRow('#/inbox?review=1', 'Jev was unsure', data.review.length));
  if (data.later_total) later.push(linkRow('#/inbox?priority=medium', 'Medium priority', data.later_total));

  root.innerHTML = `
    <div class="page-head">
      <h1>${n ? `${n} ${n === 1 ? 'email needs' : 'emails need'} you` : 'Nothing needs you'}</h1>
      ${n || !denom ? '' : `<p>Urgent and high-priority mail is done. New mail is checked every ${Math.round((s.poll_seconds || 120) / 60)} min.</p>`}
      ${denom ? `<div class="progress-line"><div class="progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Done today"><i style="width:${pct}%"></i></div><span class="num">${handled} of ${denom} done today</span></div>` : ''}
    </div>

    ${n ? `<div class="focus-list" id="focus">${cards}</div>` : ''}

    ${data.drafts.length ? `<section class="section">
      <div class="section-head"><h2>Drafts ready</h2><span class="spacer"></span><a href="#/drafts" class="link">All drafts ${icon('chev')}</a></div>
      <div class="list">${data.drafts.slice(0, 3).map(draftRow).join('')}</div>
    </section>` : ''}

    ${later.length ? `<section class="section">
      <div class="section-head"><h2>Later</h2></div>
      <nav class="linkrows">${later.join('')}</nav>
    </section>` : ''}`;
}

const linkRow = (href, label, count) => `<a href="${href}"><span>${esc(label)}</span><span class="n">${count}</span>${icon('chev')}</a>`;

function focusCard(e, active) {
  const why = whyList(e).slice(0, 2).join(' · ');
  const drafting = store.status?.drafting_enabled;
  return `<article class="focus-card ${active ? 'active' : ''}" data-id="${esc(e.id)}" data-p="${esc(e.priority)}" tabindex="0">
    <div class="top">${e.priority === 'urgent' ? '<span class="badge urgent">Urgent</span>' : ''}<span class="from">${esc(e.sender_name || e.sender_email)}</span><span class="time">${esc(fmtTime(e.received))}</span></div>
    <h3 class="subject">${esc(e.subject || '(no subject)')}</h3>
    ${why ? `<p class="why">${esc(why)}</p>` : ''}
    <p class="snippet">${esc(snippet(e.body_text, 220))}</p>
    <div class="actions">
      <button class="btn primary" data-act="handled">${icon('check')}Mark done</button>
      ${e.ready_drafts ? `<button class="btn" data-act="detail">${icon('drafts')}Review draft</button>` : drafting ? `<button class="btn" data-act="draft">${icon('pen')}Draft reply</button>` : ''}
      <button class="btn ghost" data-act="open">${icon('open')}Open in Outlook</button>
    </div>
  </article>`;
}

function draftRow(d) {
  return `<div class="rowitem" data-id="${esc(d.email_id)}" data-draft="${d.id}" data-p="${esc(d.priority || 'medium')}" role="button" tabindex="0">
    <span class="pdot"></span>
    <span class="from">To ${esc(d.sender_name || d.sender_email)}</span>
    <span class="mid"><span class="subj">${esc(d.subject)}</span></span>
    <span class="right">${d.passed ? '' : '<span class="badge warn">Check before sending</span>'}${d.outlook_draft_id ? `<button class="btn sm" data-dact="open">Open in Outlook</button>` : ''}</span>
  </div>`;
}

function wire() {
  on(root, 'click', '[data-run]', (_, b) => checkInbox(+b.dataset.run));
  on(root, 'click', '.focus-card', async (ev, card) => {
    const b = ev.target.closest('[data-act]');
    if (b) { ev.stopPropagation(); await cardAction(card, b.dataset.act); return; }
    openDetail(card.dataset.id);
  });
  on(root, 'keydown', '.focus-card', (ev, card) => { if (ev.key === 'Enter' && ev.target === card) openDetail(card.dataset.id); });
  on(root, 'click', '.rowitem', (ev, r) => { if (!ev.target.closest('button')) openDetail(r.dataset.id); });
  on(root, 'keydown', '.rowitem', (ev, r) => { if (ev.key === 'Enter' && ev.target === r) openDetail(r.dataset.id); });
  wireDraftButtons(root, () => load());
}

async function cardAction(card, act) {
  const id = card.dataset.id;
  const path = `/emails/${encodeURIComponent(id)}`;
  try {
    if (act === 'detail') openDetail(id);
    else if (act === 'open') await api(`${path}/open`, { method: 'POST' });
    else if (act === 'draft') { openDetail(id); setTimeout(() => $('#sheet [data-act=draft]')?.click(), 400); }
    else if (act === 'handled') {
      card.classList.add('leaving');
      await api(`${path}/handled`, { method: 'POST', body: { handled: true } });
      toast('Marked done', { ms: 6000, action: { label: 'Undo', onClick: async () => { await api(`${path}/handled`, { method: 'POST', body: { handled: false } }); load(); } } });
      setTimeout(load, 200);
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
