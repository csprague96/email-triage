// Drafts: replies written in your voice, waiting in Outlook Drafts for you to read and send.
import { $, api, bus, cap, esc, fmtTime, icon, on } from '../ui.js';
import { openDetail, wireDraftButtons } from '../detail.js';

let root = null;
let status = 'ready';

export async function render(el) {
  root = el;
  root.innerHTML = `
    <p class="muted" style="margin-bottom:16px">Saved in Outlook as replies. Open one, edit, and send it yourself.</p>
    <div class="toolbar">
      <div class="seg" id="statusSeg">
        ${[['ready', 'Ready'], ['opened', 'Opened'], ['done', 'Done'], ['all', 'All']].map(([v, l]) => `<button data-s="${v}" class="${v === status ? 'on' : ''}">${l}</button>`).join('')}
      </div>
    </div>
    <div id="dlist"></div>`;
  on(root, 'click', '[data-s]', (_, b) => { status = b.dataset.s; root.querySelectorAll('[data-s]').forEach((x) => x.classList.toggle('on', x === b)); load(); });
  on(root, 'click', '[data-view]', (_, b) => openDetail(b.dataset.view));
  wireDraftButtons(root, () => load());
  await load();
  const off = bus.on('drafts-changed', load);
  return () => { off(); root = null; };
}

async function load() {
  if (!root) return;
  const list = $('#dlist', root);
  let rows;
  try { rows = await api('/drafts', { query: { status } }); } catch (e) { list.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`; return; }
  if (!rows.length) {
    list.innerHTML = `<div class="empty"><h3>${status === 'ready' ? 'No drafts ready' : 'No drafts here'}</h3>${status === 'ready' ? '<p>Drafts are written for urgent and high-priority mail that needs a reply. You can also draft from any email.</p>' : ''}</div>`;
    return;
  }
  list.innerHTML = rows.map((d) => `
    <div class="draft" data-draft="${d.id}">
      <div class="meta"><b>${esc(d.subject)}</b><span>To ${esc(d.sender_name || d.sender_email)} · ${esc(fmtTime(d.created_at))}</span>${d.status === 'ready' ? '' : `<span class="badge">${esc(cap(d.status))}</span>`}${d.passed ? '' : '<span class="badge warn">Check before sending</span>'}</div>
      <pre>${esc(d.body_text)}</pre>
      ${d.notes ? `<div class="notes">${esc(d.notes)}</div>` : ''}
      <div class="row">
        ${d.outlook_draft_id ? `<button class="btn sm primary" data-dact="open">${icon('open')}Open in Outlook</button>` : '<span class="muted small">Not saved to Outlook</span>'}
        <button class="btn sm" data-dact="copy">${icon('copy')}Copy</button>
        ${d.status === 'ready' || d.status === 'opened' ? `<button class="btn sm" data-dact="done">${icon('check')}Mark done</button>` : ''}
        <span class="spacer"></span>
        <button class="btn sm ghost" data-view="${esc(d.email_id)}">View email</button>
        ${d.status !== 'discarded' ? '<button class="btn sm ghost danger" data-dact="discard">Discard</button>' : ''}
      </div>
    </div>`).join('');
}
