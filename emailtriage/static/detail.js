// The email detail sheet: one email, its reasons, actions, drafts and message.
import { $, $$, api, avatar, bus, copyText, esc, fmtDateTime, icon, on, PRIORITIES, pill, store, tagChip, toast, whyList } from './ui.js';

let openId = null;

export function isDetailOpen() { return openId !== null; }

export function closeDetail() {
  openId = null;
  $('#sheet').classList.add('hidden');
  $('#sheet-backdrop').classList.add('hidden');
  $('#sheet').innerHTML = '';
}

export async function openDetail(id) {
  openId = id;
  const sheet = $('#sheet');
  sheet.classList.remove('hidden');
  $('#sheet-backdrop').classList.remove('hidden');
  sheet.innerHTML = '<p class="muted" style="padding:20px">Loading…</p>';
  let e;
  try { e = await api('/emails/' + encodeURIComponent(id)); } catch (err) { sheet.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  if (openId !== id) return;
  render(sheet, e);
  sheet.scrollTop = 0;
  $('.close', sheet)?.focus();
}
$('#sheet-backdrop').addEventListener('click', closeDetail);

function render(sheet, e) {
  const s = store.status || {};
  const why = whyList(e);
  const probs = (s.priorities || PRIORITIES).slice().reverse().map((p) => bar(p, e.probabilities?.[p] || 0)).join('');
  const sig = Object.entries(e.signals || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => bar(k.replace(/_/g, ' '), v)).join('');
  const allTags = (store.tags?.tags || []).map((t) => t.name);
  const tagButtons = allTags.map((t) => {
    const onIt = e.tags.includes(t);
    const score = e.tag_scores?.[t];
    return `<button class="chip ${onIt ? 'on' : ''}" data-tag="${esc(t)}" title="${score != null ? Math.round(score * 100) + '% match' : ''}">
      <span class="dot" style="background:${esc(store.tagColor(t))}"></span>${esc(t)}</button>`;
  }).join('');
  const thread = e.thread;
  const handled = !!e.handled_at;

  const inner = document.createElement('div');
  inner.innerHTML = `
    <button class="btn ghost icon close" data-act="close" aria-label="Close">${icon('close')}</button>
    <div class="head">
      <div class="row">${pill(e.priority)}${e.user_priority ? '<span class="muted small">set by you</span>' : ''}${handled ? '<span class="flag" style="font-size:.78rem;color:var(--ok);font-weight:600">Handled</span>' : ''}</div>
      <h2 class="subject">${esc(e.subject || '(no subject)')}</h2>
      <div class="from">${avatar(e.sender_name, e.sender_email)}<div><div class="name">${esc(e.sender_name || e.sender_email)}</div><div class="addr">${esc(e.sender_email)} · ${esc(fmtDateTime(e.received))}${e.cc?.length ? ` · ${e.cc.length} cc` : ''}${e.has_attachments ? ' · attachment' : ''}</div></div></div>
      <div class="primary-actions">
        <button class="btn ${handled ? 'done' : 'primary'}" data-act="handled">${icon(handled ? 'undo' : 'check')}${handled ? 'Undo handled' : 'Mark handled'}</button>
        <button class="btn" data-act="open">${icon('open')}Open in Outlook</button>
        ${s.drafting_enabled ? `<button class="btn" data-act="draft">${icon('pen')}Draft a reply</button>` : ''}
        <button class="btn ghost" data-act="reassess" title="Ask Jev again with the current tags and priority definitions">${icon('refresh')}Re-assess</button>
      </div>
    </div>

    <div class="block">
      <h4>Why it landed here</h4>
      ${why.length ? `<div class="why"><ul>${why.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : '<p class="muted small">No strong signals either way.</p>'}
      ${e.adjusted_by_rule ? `<p class="note" style="margin-top:10px">${esc(e.adjusted_by_rule)}</p>` : ''}
      ${thread && (thread.note || thread.messages > 1) ? `<p class="note" style="margin-top:10px">Thread of ${thread.messages} message${thread.messages === 1 ? '' : 's'}. ${esc(thread.note || (thread.is_latest ? 'This is the latest message.' : ''))}</p>` : ''}
    </div>

    <div class="block">
      <h4>Priority <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· change it and Jev learns from the correction</span></h4>
      <div class="seg" role="radiogroup">${PRIORITIES.slice().reverse().map((p) => `<button data-p="${p}" data-setp="${p}" class="${p === e.priority ? 'on' : ''}" role="radio" aria-checked="${p === e.priority}">${p}</button>`).join('')}</div>
    </div>

    <div class="block">
      <h4>Tags <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· click to correct</span></h4>
      <div class="chips">${tagButtons || '<span class="muted small">No tags configured yet. Add some in Settings.</span>'}</div>
    </div>

    ${(e.drafts || []).length ? `<div class="block"><h4>Drafts</h4><div id="draftArea">${e.drafts.map(renderDraft).join('')}</div></div>` : ''}

    <div class="block hidden" id="composer">
      <h4>Anything the drafter should know?</h4>
      <textarea id="ctx" placeholder="Optional. Facts to use, e.g. 'release moved to 15 Oct', 'sandbox credentials went out Monday'."></textarea>
      <div class="row" style="margin-top:10px"><label class="switch"><input type="checkbox" id="replyAll"><span class="track"></span>Reply all</label><span style="flex:1"></span><button class="btn primary" data-act="godraft">${icon('sparkle')}Write the draft</button></div>
      <p class="hint" style="margin-top:8px">The draft is saved to your Outlook Drafts folder as a reply. Jev checks it for AI-sounding wording, invented facts and whether it answers the ask. Nothing is sent.</p>
    </div>

    <div class="block">
      <h4>Message</h4>
      <pre class="body">${esc(e.body_text || '(empty)')}</pre>
    </div>

    <div class="block">
      <details class="adv"><summary>Jev's numbers (confidence ${Math.round((e.confidence || 0) * 100)}%, time sensitivity ${e.time_sensitivity ?? 0} of 3)</summary>
        <div class="grid2" style="margin-top:12px">
          <div><h4>Priority probabilities</h4><div class="bars">${probs}</div></div>
          <div><h4>Signals</h4><div class="bars">${sig || '<span class="muted small">none</span>'}</div></div>
        </div>
        ${Object.keys(e.tag_scores || {}).length ? `<div style="margin-top:14px"><h4>Tag scores</h4><div class="bars">${Object.entries(e.tag_scores).sort((a, b) => b[1] - a[1]).map(([k, v]) => bar(k, v)).join('')}</div></div>` : ''}
      </details>
    </div>`;
  sheet.replaceChildren(inner);
  wire(inner, e);
}

function bar(label, v) {
  return `<div class="bar"><span>${esc(label)}</span><span class="track"><i style="width:${Math.round(v * 100)}%"></i></span><span class="v">${Math.round(v * 100)}%</span></div>`;
}

export function renderDraft(dr) {
  const checks = Object.entries(dr.checks || {}).map(([k, v]) => {
    const label = k.replace(/_/g, ' ');
    if (k === 'quality') return `<span class="check ${v >= 2 ? 'good' : 'bad'}">quality ${v} / 3</span>`;
    const bad = (k === 'sounds_like_ai' && v >= 0.6) || (k === 'invents_facts' && v >= 0.5) || (k === 'answers_the_ask' && v < 0.5) || (k === 'matches_voice' && v < 0.4);
    return `<span class="check ${bad ? 'bad' : 'good'}">${esc(label)} ${Math.round(v * 100)}%</span>`;
  }).join('');
  return `<div class="draft ${dr.passed ? '' : 'failed'}" data-draft="${dr.id}">
    <div class="meta"><b>${esc(dr.status)}</b><span>${dr.attempts} attempt${dr.attempts > 1 ? 's' : ''}</span>${dr.passed ? '<span style="color:var(--ok)">passed Jev\'s checks</span>' : '<span style="color:var(--warn)">checks flagged, read carefully</span>'}</div>
    <pre>${esc(dr.body_text)}</pre>
    <div class="checks">${checks}</div>
    ${dr.notes ? `<div class="notes">${esc(dr.notes)}</div>` : ''}
    <div class="row">
      ${dr.outlook_draft_id ? `<button class="btn sm primary" data-dact="open">${icon('open')}Open in Outlook</button>` : '<span class="muted small">not saved to the mailbox</span>'}
      <button class="btn sm" data-dact="copy">${icon('copy')}Copy</button>
      <button class="btn sm ok" data-dact="done">${icon('check')}Done</button>
      <button class="btn sm ghost danger" data-dact="discard">Discard</button>
    </div></div>`;
}

export function wireDraftButtons(root, after) {
  on(root, 'click', '[data-draft] [data-dact]', async (ev, b) => {
    ev.stopPropagation();
    const box = b.closest('[data-draft]');
    const id = box.dataset.draft;
    const act = b.dataset.dact;
    try {
      if (act === 'open') await api(`/drafts/${id}/open`, { method: 'POST' });
      else if (act === 'copy') { await copyText($('pre', box).textContent); return; }
      else if (act === 'done') await api(`/drafts/${id}/done`, { method: 'POST' });
      else if (act === 'discard') { if (!confirm('Discard this draft? It is also deleted from Outlook Drafts.')) return; await api(`/drafts/${id}`, { method: 'DELETE' }); }
      bus.emit('drafts-changed');
      after?.(act, id);
    } catch (er) { toast(er.message, { kind: 'error', ms: 5000 }); }
  });
}

function wire(sheet, e) {
  const id = e.id;
  const path = (suffix) => `/emails/${encodeURIComponent(id)}${suffix}`;
  const refresh = () => { bus.emit('emails-changed'); openDetail(id); };

  on(sheet, 'click', '[data-act]', async (_, el) => {
    const act = el.dataset.act;
    try {
      if (act === 'close') closeDetail();
      else if (act === 'handled') { await api(path('/handled'), { method: 'POST', body: { handled: !e.handled_at } }); toast(e.handled_at ? 'Back on the list' : 'Handled', { kind: e.handled_at ? '' : 'ok' }); refresh(); }
      else if (act === 'open') await api(path('/open'), { method: 'POST' });
      else if (act === 'reassess') { toast('Asking Jev…'); await api(path('/reassess'), { method: 'POST' }); refresh(); }
      else if (act === 'draft') { $('#composer', sheet).classList.remove('hidden'); $('#ctx', sheet).focus(); }
      else if (act === 'godraft') {
        el.disabled = true;
        const t = toast('Writing a draft and having Jev check it…', { ms: 60000 });
        try {
          await api(path('/draft'), { method: 'POST', body: { context: $('#ctx', sheet).value, reply_all: $('#replyAll', sheet).checked } });
          t.remove(); toast('Draft saved to Outlook Drafts', { kind: 'ok' }); bus.emit('drafts-changed'); refresh();
        } catch (er) { t.remove(); el.disabled = false; throw er; }
      }
    } catch (er) { toast(er.message, { kind: 'error', ms: 6000 }); }
  });
  on(sheet, 'click', '[data-setp]', async (_, b) => {
    const p = b.dataset.setp;
    if (p === e.priority) return;
    try { await api(path('/priority'), { method: 'POST', body: { priority: p } }); toast(`Set to ${p}. Jev will see this as an example.`, { kind: 'ok' }); refresh(); }
    catch (er) { toast(er.message, { kind: 'error' }); }
  });
  on(sheet, 'click', '[data-tag]', async (_, b) => {
    b.classList.toggle('on');
    const tags = $$('[data-tag].on', sheet).map((x) => x.dataset.tag);
    try { await api(path('/tags'), { method: 'POST', body: { tags } }); bus.emit('emails-changed'); }
    catch (er) { toast(er.message, { kind: 'error' }); }
  });
  wireDraftButtons(sheet, () => refresh());
}
