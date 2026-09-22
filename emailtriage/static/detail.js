// The email detail sheet. Reading order: what it is and what to do, why, the draft, the message,
// then the tools for correcting Jev.
import { $, $$, api, avatar, bus, cap, copyText, esc, fmtDateTime, icon, on, PRIORITIES, pill, store, toast, whyList } from './ui.js';

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
  sheet.innerHTML = '<p class="muted">Loading</p>';
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
  const probs = (s.priorities || PRIORITIES).slice().reverse().map((p) => bar(cap(p), e.probabilities?.[p] || 0)).join('');
  const sig = Object.entries(e.signals || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => bar(cap(k.replace(/_/g, ' ')), v)).join('');
  const allTags = (store.tags?.tags || []).map((t) => t.name);
  const tagButtons = allTags.map((t) => {
    const onIt = e.tags.includes(t);
    const score = e.tag_scores?.[t];
    return `<button class="chip ${onIt ? 'on' : ''}" data-tag="${esc(t)}" aria-pressed="${onIt}" title="${score != null ? Math.round(score * 100) + '% match' : ''}">
      <span class="dot" style="background:${esc(store.tagColor(t))}"></span>${esc(t)}</button>`;
  }).join('');
  const thread = e.thread;
  const handled = !!e.handled_at;

  const notes = [
    e.adjusted_by_rule ? esc(e.adjusted_by_rule) : '',
    thread && (thread.note || thread.messages > 1) ? `Thread of ${thread.messages} message${thread.messages === 1 ? '' : 's'}. ${esc(thread.note || (thread.is_latest ? 'This is the latest message.' : ''))}` : '',
  ].filter(Boolean);

  const inner = document.createElement('div');
  inner.innerHTML = `
    <button class="btn ghost icon close" data-act="close" aria-label="Close">${icon('close')}</button>
    <div class="head">
      <div class="row">${pill(e.priority)}${handled ? '<span class="badge ok">Done</span>' : ''}</div>
      <h2 class="subject">${esc(e.subject || '(no subject)')}</h2>
      <div class="from">${avatar(e.sender_name, e.sender_email)}<div><div class="name">${esc(e.sender_name || e.sender_email)}</div><div class="addr">${esc(e.sender_email)} · ${esc(fmtDateTime(e.received))}${e.cc?.length ? ` · ${e.cc.length} cc` : ''}${e.has_attachments ? ' · Attachment' : ''}</div></div></div>
      <div class="primary-actions">
        <button class="btn ${handled ? 'done' : 'primary'}" data-act="handled">${icon(handled ? 'undo' : 'check')}${handled ? 'Undo done' : 'Mark done'}</button>
        ${s.drafting_enabled ? `<button class="btn" data-act="draft">${icon('pen')}Draft reply</button>` : ''}
        <button class="btn ghost" data-act="open">${icon('open')}Open in Outlook</button>
      </div>
    </div>

    <div class="block">
      <h4>Why it's ${esc(e.priority)}</h4>
      <p class="why-line">${why.length ? esc(why.join(' · ')) : 'No strong signals either way.'}</p>
      ${notes.map((n) => `<p class="note">${n}</p>`).join('')}
    </div>

    ${(e.drafts || []).length ? `<div class="block"><h4>Draft</h4><div id="draftArea">${e.drafts.map(renderDraft).join('')}</div></div>` : ''}

    <div class="block hidden" id="composer">
      <label class="field" for="ctx">Notes for the draft <span class="hint">Optional. Facts to include, like a new date or who owns the next step.</span></label>
      <textarea id="ctx"></textarea>
      <div class="row"><label class="switch"><input type="checkbox" id="replyAll"><span class="track"></span>Reply all</label><span style="flex:1"></span><button class="btn primary" data-act="godraft">${icon('pen')}Write draft</button></div>
      <p class="hint">Saved to Outlook Drafts. Jev checks it before you see it. Nothing is sent.</p>
    </div>

    <div class="block">
      <h4>Message</h4>
      <pre class="body">${esc(e.body_text || '(empty)')}</pre>
    </div>

    <div class="block">
      <div class="row between"><h4>Correct Jev</h4><button class="btn ghost sm" data-act="reassess" title="Ask Jev again with the current tags and priority definitions">${icon('refresh')}Re-assess</button></div>
      <p class="hint">Changes are saved as examples Jev learns from.</p>
      <div class="seg" role="radiogroup" aria-label="Priority">${PRIORITIES.slice().reverse().map((p) => `<button data-p="${p}" data-setp="${p}" class="${p === e.priority ? 'on' : ''}" role="radio" aria-checked="${p === e.priority}">${cap(p)}</button>`).join('')}</div>
      <div class="chips" aria-label="Tags">${tagButtons || '<span class="muted small">No tags yet. Add them in Settings.</span>'}</div>
      <details class="adv"><summary>Jev's numbers: ${Math.round((e.confidence || 0) * 100)}% confident, time sensitivity ${e.time_sensitivity ?? 0} of 3</summary>
        <div class="grid2">
          <div class="bars"><h4>Priority</h4>${probs}</div>
          <div class="bars"><h4>Signals</h4>${sig || '<span class="muted small">None</span>'}</div>
        </div>
        ${Object.keys(e.tag_scores || {}).length ? `<div class="bars" style="margin-top:16px"><h4>Tags</h4>${Object.entries(e.tag_scores).sort((a, b) => b[1] - a[1]).map(([k, v]) => bar(k, v)).join('')}</div>` : ''}
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
    const label = cap(k.replace(/_/g, ' '));
    if (k === 'quality') return `<span class="badge ${v >= 2 ? 'ok' : 'warn'}">Quality ${v} of 3</span>`;
    const bad = (k === 'sounds_like_ai' && v >= 0.6) || (k === 'invents_facts' && v >= 0.5) || (k === 'answers_the_ask' && v < 0.5) || (k === 'matches_voice' && v < 0.4);
    return `<span class="badge ${bad ? 'warn' : 'ok'}">${esc(label)} ${Math.round(v * 100)}%</span>`;
  }).join('');
  const meta = (dr.status === 'ready' ? '' : `<span class="badge">${esc(cap(dr.status))}</span>`) + (dr.passed ? '' : '<span class="badge warn">Check before sending</span>');
  return `<div class="draft" data-draft="${dr.id}">
    ${meta ? `<div class="meta">${meta}</div>` : ''}
    <pre>${esc(dr.body_text)}</pre>
    ${dr.notes ? `<div class="notes">${esc(dr.notes)}</div>` : ''}
    <div class="row">
      ${dr.outlook_draft_id ? `<button class="btn sm primary" data-dact="open">${icon('open')}Open in Outlook</button>` : '<span class="muted small">Not saved to Outlook</span>'}
      <button class="btn sm" data-dact="copy">${icon('copy')}Copy</button>
      <button class="btn sm" data-dact="done">${icon('check')}Mark done</button>
      <span class="spacer"></span>
      <button class="btn sm ghost danger" data-dact="discard">Discard</button>
    </div>
    ${checks ? `<details class="adv"><summary>Jev's checks${dr.attempts > 1 ? `, ${dr.attempts} attempts` : ''}</summary><div class="checks">${checks}</div></details>` : ''}
  </div>`;
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
      else if (act === 'handled') { await api(path('/handled'), { method: 'POST', body: { handled: !e.handled_at } }); toast(e.handled_at ? 'Back on the list' : 'Marked done'); refresh(); }
      else if (act === 'open') await api(path('/open'), { method: 'POST' });
      else if (act === 'reassess') { toast('Asking Jev again'); await api(path('/reassess'), { method: 'POST' }); refresh(); }
      else if (act === 'draft') { $('#composer', sheet).classList.remove('hidden'); $('#ctx', sheet).focus(); }
      else if (act === 'godraft') {
        el.disabled = true;
        const t = toast('Writing the draft. Jev checks it before it is saved.', { ms: 60000 });
        try {
          await api(path('/draft'), { method: 'POST', body: { context: $('#ctx', sheet).value, reply_all: $('#replyAll', sheet).checked } });
          t.remove(); toast('Draft saved to Outlook'); bus.emit('drafts-changed'); refresh();
        } catch (er) { t.remove(); el.disabled = false; throw er; }
      }
    } catch (er) { toast(er.message, { kind: 'error', ms: 6000 }); }
  });
  on(sheet, 'click', '[data-setp]', async (_, b) => {
    const p = b.dataset.setp;
    if (p === e.priority) return;
    try { await api(path('/priority'), { method: 'POST', body: { priority: p } }); toast(`Set to ${p}. Saved as an example for Jev.`); refresh(); }
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
