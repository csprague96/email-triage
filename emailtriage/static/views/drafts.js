// Drafts: replies written in your voice, waiting in Outlook Drafts for you to read and send.
import { $, api, bus, esc, fmtTime, icon, on, pill, snippet } from '../ui.js';
import { openDetail, wireDraftButtons } from '../detail.js';

let root = null;
let status = 'ready';

export async function render(el) {
  root = el;
  root.innerHTML = `
    <p class="muted" style="max-width:720px;margin-bottom:16px">Each draft is saved in your Outlook Drafts folder as a real reply to the original email, quoted thread and signature included. Open it, read it, edit, send. Nothing is ever sent for you.</p>
    <div class="toolbar">
      <div class="seg" id="statusSeg">
        ${[['ready', 'Ready to review'], ['opened', 'Opened'], ['done', 'Done'], ['superseded', 'Superseded'], ['discarded', 'Discarded'], ['all', 'All']].map(([v, l]) => `<button data-s="${v}" class="${v === status ? 'on' : ''}">${l}</button>`).join('')}
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
    list.innerHTML = `<div class="empty"><div class="big">✎</div><h3>No drafts ${status === 'ready' ? 'waiting' : 'here'}</h3><p>${status === 'ready' ? 'Drafts are written automatically for urgent and high-priority mail that needs a reply. You can also ask for one from any email\'s details.' : ''}</p></div>`;
    return;
  }
  list.innerHTML = rows.map((d) => `
    <div class="draft ${d.passed ? '' : 'failed'}" data-draft="${d.id}">
      <div class="meta">${pill(d.priority || 'medium')}<b>${esc(d.subject)}</b><span>to ${esc(d.sender_name || d.sender_email)}</span><span>${esc(fmtTime(d.created_at))}</span><span>${esc(d.status)}</span>${d.passed ? '<span style="color:var(--ok)">passed checks</span>' : '<span style="color:var(--warn)">checks flagged</span>'}</div>
      <pre>${esc(d.body_text)}</pre>
      ${d.notes ? `<div class="notes">${esc(d.notes)}</div>` : ''}
      <div class="row">
        ${d.outlook_draft_id ? `<button class="btn sm primary" data-dact="open">${icon('open')}Open in Outlook</button>` : '<span class="muted small">not saved to the mailbox</span>'}
        <button class="btn sm" data-dact="copy">${icon('copy')}Copy</button>
        ${d.status === 'ready' || d.status === 'opened' ? `<button class="btn sm ok" data-dact="done">${icon('check')}Done</button>` : ''}
        ${d.status !== 'discarded' ? '<button class="btn sm ghost danger" data-dact="discard">Discard</button>' : ''}
        <span style="flex:1"></span>
        <button class="btn sm ghost" data-view="${esc(d.email_id)}">View email ${icon('chev')}</button>
      </div>
    </div>`).join('');
}
