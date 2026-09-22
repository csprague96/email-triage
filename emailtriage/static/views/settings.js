// Settings: account, triage behaviour, digest & Slack, tags, priorities, drafting prompt, appearance.
import { $, $$, api, bus, cap, esc, fmtTime, icon, on, PRIORITIES, pill, store, toast } from '../ui.js';
import { navigate, refreshStatus, setTheme, themePref, checkInbox } from '../app.js';

const SECTIONS = [
  ['account', 'Account'],
  ['triage', 'Triage'],
  ['digest', 'Digest & Slack'],
  ['tags', 'Tags'],
  ['priorities', 'Priorities'],
  ['drafting', 'Drafting'],
  ['appearance', 'Appearance'],
];
const MASK = '••••••••';
let root = null;
let settings = null;

export async function render(el, route) {
  root = el;
  const section = SECTIONS.some(([id]) => id === route.sub) ? route.sub : 'account';
  root.innerHTML = `<div class="settings">
    <nav class="subnav">${SECTIONS.map(([id, label]) => `<a href="#/settings/${id}" class="${id === section ? 'active' : ''}">${label}</a>`).join('')}</nav>
    <div class="form" id="pane"><p class="muted">Loading</p></div></div>`;
  try { settings = await api('/settings'); } catch (e) { $('#pane', root).innerHTML = `<p>${esc(e.message)}</p>`; return; }
  await ({ account, triage, digest, tags, priorities, drafting, appearance })[section]($('#pane', root));
  return () => { root = null; };
}

// ---- helpers --------------------------------------------------------------------
function field(key, label, { type = 'text', hint = '', placeholder = '', min, max, step } = {}) {
  const v = settings[key];
  return `<label class="field">${label}
    <input type="${type}" data-key="${key}" value="${esc(v ?? '')}" placeholder="${esc(placeholder)}" ${min != null ? `min="${min}"` : ''} ${max != null ? `max="${max}"` : ''} ${step != null ? `step="${step}"` : ''}>
    ${hint ? `<span class="hint">${hint}</span>` : ''}</label>`;
}
function secret(key, label, { hint = '', placeholder = '' } = {}) {
  const v = settings[key] || {};
  const ph = v.set ? `Saved${v.hint ? ', ends in …' + esc(v.hint) : ''}. Paste a new value to replace it.` : placeholder;
  return `<label class="field">${label}
    <div class="secret"><input type="password" data-key="${key}" data-secret="1" value="" placeholder="${ph}" autocomplete="off"><button type="button" class="btn icon" data-reveal aria-label="Show">${icon('eye')}</button>${v.set ? `<button type="button" class="btn ghost danger sm" data-clear="${key}">Clear</button>` : ''}</div>
    ${hint ? `<span class="hint">${hint}</span>` : ''}</label>`;
}
function toggle(key, label, sub = '') {
  return `<label class="switch"><input type="checkbox" data-key="${key}" ${settings[key] ? 'checked' : ''}><span class="track"></span><span>${label}${sub ? `<span class="sub">${sub}</span>` : ''}</span></label>`;
}
function saveBar(id = 'save') {
  return `<div class="actions"><span class="dirty hidden" data-dirty>Unsaved changes</span><button class="btn primary" data-save="${id}">Save</button></div>`;
}
function collect(pane) {
  const out = {};
  $$('[data-key]', pane).forEach((el) => {
    const key = el.dataset.key;
    if (!key) return;
    if (el.type === 'checkbox') out[key] = el.checked;
    else if (el.dataset.secret) { if (el.value !== '') out[key] = el.value; }
    else out[key] = el.value;
  });
  $$('[data-clear-pending]', pane).forEach((el) => { out[el.dataset.clearPending] = ''; });
  return out;
}
function wireForm(pane, { after } = {}) {
  pane.addEventListener('input', () => $('[data-dirty]', pane)?.classList.remove('hidden'));
  pane.addEventListener('change', () => $('[data-dirty]', pane)?.classList.remove('hidden'));
  on(pane, 'click', '[data-reveal]', (_, b) => { const i = b.previousElementSibling; i.type = i.type === 'password' ? 'text' : 'password'; });
  on(pane, 'click', '[data-clear]', (_, b) => {
    const key = b.dataset.clear;
    const marker = document.createElement('span');
    marker.dataset.clearPending = key; marker.className = 'hint'; marker.textContent = 'Will be cleared on save.';
    b.replaceWith(marker);
    $('[data-dirty]', pane)?.classList.remove('hidden');
  });
  on(pane, 'click', '[data-save]', async (_, b) => {
    b.disabled = true;
    try {
      const body = collect(pane);
      const r = await api('/settings', { method: 'PUT', body });
      settings = r.settings;
      toast(r.changed.length ? 'Saved. Takes effect on the next inbox check.' : 'Nothing changed', { kind: r.changed.length ? 'ok' : '' });
      $('[data-dirty]', pane)?.classList.add('hidden');
      await refreshStatus();
      after?.(r.changed);
    } catch (e) { toast(e.message, { kind: 'error', ms: 7000 }); }
    finally { b.disabled = false; }
  });
}

// ---- Account ----------------------------------------------------------------------
async function account(pane) {
  const s = store.status || {};
  const a = store.auth || {};
  const graph = settings.MAIL_BACKEND === 'graph';
  pane.innerHTML = `
    <div class="card pad">
      <h3>Mailbox</h3>
      <dl class="kv">
        <dt>Status</dt><dd><span class="dot ${s.backend_ok ? '' : 'bad'}" style="display:inline-block;margin-right:6px"></span>${s.backend_ok ? 'Connected' : 'Not connected'}${s.backend_ok ? '' : a.error ? ` · <span class="muted">${esc(a.error)}</span>` : ''}</dd>
        <dt>Account</dt><dd>${esc(s.owner_name || a.name || '')} <span class="muted">${esc(s.owner_email || a.account || '')}</span></dd>
        <dt>Source</dt><dd>${graph ? 'Microsoft 365 (Graph API)' : 'Classic Outlook on this PC'}</dd>
      </dl>
      <div class="row">
        ${graph
          ? (a.signed_in ? `<button class="btn" data-auth="signout">Sign out</button>` : `<button class="btn primary" data-auth="signin">Sign in with Microsoft</button>`)
          : `<button class="btn" data-auth="reconnect">${icon('refresh')}Reconnect Outlook</button>`}
      </div>
    </div>

    <div class="card pad">
      <h3>Where mail comes from</h3>
      <div class="theme-options" style="grid-template-columns:1fr 1fr">
        <label class="theme-option ${!graph ? 'on' : ''}"><input type="radio" name="backend" data-key="MAIL_BACKEND" value="outlook" ${!graph ? 'checked' : ''} class="sr-only"><b>Classic Outlook</b><span class="hint">Reads the Outlook already signed in on this PC. Zero setup. Windows only.</span></label>
        <label class="theme-option ${graph ? 'on' : ''}"><input type="radio" name="backend" data-key="MAIL_BACKEND" value="graph" ${graph ? 'checked' : ''} class="sr-only"><b>Microsoft 365 sign-in</b><span class="hint">Works with new Outlook, Mac, or a server. Needs an Entra app registration from your IT team.</span></label>
      </div>
      <div id="graphFields" class="${graph ? '' : 'hidden'}" style="display:grid;gap:14px">
        ${field('GRAPH_CLIENT_ID', 'Entra application (client) ID', { hint: 'From the app registration. Delegated permissions Mail.ReadWrite and User.Read, public client flows enabled.', placeholder: '00000000-0000-0000-0000-000000000000' })}
        ${field('GRAPH_TENANT_ID', 'Tenant', { hint: '"common" works for most organisations, or paste your tenant ID.' })}
      </div>
      ${saveBar()}
    </div>

    <div class="card pad">
      <h3>AI services</h3>
      <p class="hint">Jev (TypeSafe) does the sorting and tagging and checks drafts. OpenAI only writes reply drafts. Keys are stored in the .env file on this PC and never shown again here.</p>
      ${secret('TYPESAFE_API_KEY', 'TypeSafe API key', { hint: 'Required. Get one at console.typesafe.ai', placeholder: 'Paste your key' })}
      ${field('TYPESAFE_MODEL', 'Jev model', { hint: 'Leave as jev-latest unless TypeSafe tells you otherwise.' })}
      ${secret('OPENAI_API_KEY', 'OpenAI API key', { hint: 'Optional. Without it you get priorities and tags but no drafts.', placeholder: 'Paste your key, or leave empty' })}
      <dl class="kv"><dt>Jev</dt><dd><span class="dot ${s.jev_configured ? '' : 'bad'}" style="display:inline-block;margin-right:6px"></span>${s.jev_configured ? 'key set' : 'no key'}</dd><dt>Drafting</dt><dd><span class="dot ${s.drafting_enabled ? '' : 'off'}" style="display:inline-block;margin-right:6px"></span>${s.drafting_enabled ? `on, ${esc(s.openai_model)}` : 'off'}</dd></dl>
      ${saveBar('keys')}
    </div>`;
  // radio to key mapping: collect() reads every [data-key]; radios need only the checked one
  const radios = $$('input[name=backend]', pane);
  radios.forEach((r) => { r.addEventListener('change', () => { radios.forEach((x) => { x.closest('.theme-option').classList.toggle('on', x.checked); x.dataset.key = x.checked ? 'MAIL_BACKEND' : ''; }); $('#graphFields', pane).classList.toggle('hidden', $('input[name=backend]:checked', pane).value !== 'graph'); }); if (!r.checked) r.dataset.key = ''; });
  wireForm(pane, { after: async (changed) => { if (changed.includes('MAIL_BACKEND') || changed.includes('GRAPH_CLIENT_ID')) { await refreshStatus(); render(root, { sub: 'account', params: {} }); } } });
  on(pane, 'click', '[data-auth]', async (_, b) => {
    try {
      if (b.dataset.auth === 'signout') { store.auth = await api('/auth/signout', { method: 'POST' }); toast('Signed out'); }
      else if (b.dataset.auth === 'signin') { store.auth = await api('/auth/connect', { method: 'POST' }); navigate('today'); return; }
      else { store.auth = await api('/auth/connect', { method: 'POST' }); toast('Connected', { kind: 'ok' }); }
      await refreshStatus(); render(root, { sub: 'account', params: {} });
    } catch (e) { toast(e.message, { kind: 'error', ms: 7000 }); }
  });
}

// ---- Triage -----------------------------------------------------------------------
async function triage(pane) {
  const s = store.status || {};
  pane.innerHTML = `
    <div class="card pad">
      <h3>Checking the inbox</h3>
      ${field('POLL_SECONDS', 'Check every (seconds)', { type: 'number', min: 30, max: 3600, step: 30, hint: 'How often the background watcher looks for new mail. 120 is a good default.' })}
      ${toggle('APPLY_OUTLOOK_CATEGORIES', 'Write categories onto emails', 'Priority: Urgent and your tag names appear as coloured categories in Outlook and on your phone.')}
      ${toggle('TOAST_NOTIFICATIONS', 'Windows notifications', 'A toast when a draft is ready or something urgent lands.')}
      ${saveBar()}
    </div>
    <div class="card pad">
      <h3>Run it now</h3>
      <p class="hint">Last check: ${s.last_run ? esc(fmtTime(s.last_run)) : 'never'}${s.last_run_note ? ` · ${esc(s.last_run_note)}` : ''}. Watcher ${s.watcher_alive ? 'is running in this process' : 'is not running in this process (the background job may be)'}.</p>
      <div class="row"><button class="btn" data-run="0">${icon('refresh')}Check inbox now</button><button class="btn" data-run="168">Re-triage the last 7 days</button></div>
    </div>`;
  wireForm(pane);
  on(pane, 'click', '[data-run]', (_, b) => checkInbox(+b.dataset.run || undefined));
}

// ---- Digest & Slack ---------------------------------------------------------------
async function digest(pane) {
  const s = store.status || {};
  pane.innerHTML = `
    <div class="card pad">
      <h3>Morning digest to Slack</h3>
      <p class="hint">A short summary posted to a Slack channel or DM: counts by priority, the urgent and high items, drafts waiting, and low-confidence items worth a look. Only subjects, sender names and tags are sent, never message bodies. Posted while the watcher is running.</p>
      ${toggle('DIGEST_ENABLED', 'Send the digest', s.slack_configured ? (s.last_digest_at ? `Last sent ${esc(fmtTime(s.last_digest_at))}` : 'Not sent yet') : 'Add a webhook below first')}
      ${secret('SLACK_WEBHOOK_URL', 'Slack incoming webhook URL', { hint: 'Create one at api.slack.com/messaging/webhooks for the channel or DM you want. Starts with https://hooks.slack.com/', placeholder: 'https://hooks.slack.com/services/…' })}
      <div class="grid2">
        ${field('DIGEST_TIME', 'Time (local)', { type: 'time' })}
        ${field('DIGEST_LOOKBACK_HOURS', 'Covers the last (hours)', { type: 'number', min: 1, max: 168 })}
      </div>
      ${toggle('DIGEST_WEEKDAYS_ONLY', 'Weekdays only')}
      ${saveBar()}
    </div>
    <div class="card pad">
      <div class="row between"><h3>Preview</h3><div class="row"><button class="btn sm" data-preview>${icon('refresh')}Refresh</button><button class="btn sm primary" data-send ${s.slack_configured ? '' : 'disabled'}>${icon('slack')}Send to Slack now</button></div></div>
      <pre class="preview" id="digestPreview">Loading</pre>
    </div>`;
  wireForm(pane);
  const preview = async () => { try { const d = await api('/digest/preview'); $('#digestPreview', pane).textContent = d.text; } catch (e) { $('#digestPreview', pane).textContent = e.message; } };
  on(pane, 'click', '[data-preview]', preview);
  on(pane, 'click', '[data-send]', async () => { try { await api('/digest/send', { method: 'POST' }); toast('Digest posted to Slack', { kind: 'ok' }); refreshStatus(); } catch (e) { toast(e.message, { kind: 'error', ms: 6000 }); } });
  preview();
}

// ---- Tags -------------------------------------------------------------------------
async function tags(pane) {
  let conf = await api('/tags');
  const draw = () => {
    const shared = conf.tags.filter((t) => t.shared);
    const local = conf.tags.filter((t) => !t.shared);
    pane.innerHTML = `
      <div class="card pad">
        <h3>My tags</h3>
        <p class="hint">Each tag is a yes/no question Jev answers about every email. Describe when the tag applies, in plain words. New tags apply to newly triaged mail; use Re-assess on an email to apply them to older ones.</p>
        <div id="tagList">
          ${local.map((t, i) => `<div class="tagedit" data-i="${i}">
            <input type="color" value="${esc(t.color || '#64748b')}" data-f="color" aria-label="Colour">
            <input type="text" value="${esc(t.name)}" data-f="name" placeholder="Tag name" aria-label="Name">
            <textarea data-f="description" placeholder="When does it apply? e.g. The email is about chargebacks, disputes, or the RCVR integration.">${esc(t.description)}</textarea>
            <input type="number" min="0.1" max="0.99" step="0.05" value="${t.threshold ?? ''}" placeholder="Threshold ${conf.default_threshold ?? 0.6}" data-f="threshold" aria-label="Threshold" title="Confidence needed for this tag (blank = default)">
            <button class="btn ghost icon danger" data-del="${i}" aria-label="Remove tag">${icon('close')}</button>
          </div>`).join('')}
        </div>
        <div class="row between">
          <button class="btn" data-add>${icon('plus')}Add tag</button>
          <label class="field" style="grid-template-columns:auto auto;align-items:center;gap:10px;display:flex">Default threshold <input type="number" id="defTh" min="0.1" max="0.99" step="0.05" value="${conf.default_threshold ?? 0.6}" style="width:90px"></label>
        </div>
        <div class="actions"><button class="btn primary" data-savetags>Save tags</button></div>
      </div>

      <div class="card pad">
        <h3>Shared team tags</h3>
        <p class="hint">Point this at a tags.json your team maintains (the raw URL of the file in your repo, or a file on a shared drive). Everyone gets the same tags, read-only here. Local tags with the same name are ignored.</p>
        ${field('SHARED_TAGS_SOURCE', 'Source URL or path', { placeholder: 'https://raw.githubusercontent.com/…/tags.json' })}
        ${field('SHARED_TAGS_REFRESH_HOURS', 'Refresh every (hours)', { type: 'number', min: 0.5, max: 168, step: 0.5 })}
        <div class="row between">
          <span class="hint">${conf.shared_source ? (conf.shared_error ? `Last sync failed: ${esc(conf.shared_error)}` : `${shared.length} shared tag${shared.length === 1 ? '' : 's'}${conf.shared_synced_at ? `, synced ${esc(fmtTime(conf.shared_synced_at))}` : ''}`) : 'Not configured'}</span>
          <div class="row"><button class="btn sm" data-sync ${conf.shared_source ? '' : 'disabled'}>${icon('refresh')}Sync now</button><button class="btn sm primary" data-save="shared">Save</button></div>
        </div>
        ${shared.length ? `<div class="list">${shared.map((t) => `<div class="rowitem static"><span class="tag" style="--tag-color:${esc(t.color || '#64748b')}"><i></i>${esc(t.name)}</span><span class="muted small">${esc(t.description)}</span></div>`).join('')}</div>` : ''}
      </div>`;
  };
  draw();
  const collectTags = () => {
    const local = $$('.tagedit', pane).map((el) => {
      const t = { name: $('[data-f=name]', el).value.trim(), description: $('[data-f=description]', el).value.trim(), color: $('[data-f=color]', el).value, shared: false };
      const th = parseFloat($('[data-f=threshold]', el).value); if (!isNaN(th)) t.threshold = th;
      return t;
    });
    conf.tags = conf.tags.filter((t) => t.shared).concat(local);
    conf.default_threshold = parseFloat($('#defTh', pane).value) || 0.6;
  };
  wireForm(pane);
  on(pane, 'click', '[data-add]', () => { collectTags(); conf.tags.push({ name: '', description: '', shared: false, color: '#' + Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, '0') }); draw(); $$('.tagedit [data-f=name]', pane).pop()?.focus(); });
  on(pane, 'click', '[data-del]', (_, b) => { collectTags(); const local = conf.tags.filter((t) => !t.shared); local.splice(+b.dataset.del, 1); conf.tags = conf.tags.filter((t) => t.shared).concat(local); draw(); });
  on(pane, 'click', '[data-savetags]', async () => { collectTags(); try { conf = await api('/tags', { method: 'PUT', body: conf }); bus.emit('tags-changed'); toast('Tags saved', { kind: 'ok' }); draw(); } catch (e) { toast(e.message, { kind: 'error', ms: 6000 }); } });
  on(pane, 'click', '[data-sync]', async () => { toast('Syncing'); try { const r = await api('/tags/sync', { method: 'POST' }); toast(r.error ? 'Sync failed: ' + r.error : `Synced ${r.tags} shared tags`, { kind: r.error ? 'error' : 'ok', ms: 5000 }); conf = await api('/tags'); bus.emit('tags-changed'); draw(); } catch (e) { toast(e.message, { kind: 'error' }); } });
}

// ---- Priorities ---------------------------------------------------------------------
async function priorities(pane) {
  const c = await api('/priority-config');
  pane.innerHTML = `
    <div class="card pad">
      <h3>What each level means</h3>
      <p class="hint">These sentences are the literal instructions Jev receives. Rewrite them about your own inbox. Corrections you make on individual emails are also fed back as examples.</p>
      ${PRIORITIES.slice().reverse().map((p) => `<div class="leveledit">${pill(p)}<textarea data-level="${p}">${esc(c.levels[p])}</textarea></div>`).join('')}
      <div class="grid2">
        <label class="field">Minimum confidence before the rules take over <input type="number" id="minConf" min="0" max="1" step="0.05" value="${c.thresholds?.min_confidence ?? 0.5}"><span class="hint">Below this, the yes/no signals pick the priority and the email is marked unsure.</span></label>
        <label class="field">Confidence needed to keep "urgent" <input type="number" id="urgentConf" min="0" max="1" step="0.05" value="${c.thresholds?.urgent_needs_confidence ?? 0.6}"><span class="hint">A shaky urgent is demoted to high.</span></label>
      </div>
      <h3 style="margin-top:8px">Signals</h3>
      <p class="hint">Extra yes/no questions asked alongside priority. Keys are fixed; reword the questions freely.</p>
      ${Object.entries(c.signals).filter(([k]) => !k.startsWith('_')).map(([k, v]) => `<label class="field">${esc(k.replace(/_/g, ' '))}<input type="text" data-signal="${esc(k)}" value="${esc(v)}"></label>`).join('')}
      <div class="actions"><button class="btn primary" data-savep>Save</button></div>
    </div>`;
  on(pane, 'click', '[data-savep]', async () => {
    $$('[data-level]', pane).forEach((t) => { c.levels[t.dataset.level] = t.value.trim(); });
    c.thresholds = { ...(c.thresholds || {}), min_confidence: parseFloat($('#minConf', pane).value), urgent_needs_confidence: parseFloat($('#urgentConf', pane).value) };
    $$('[data-signal]', pane).forEach((i) => { c.signals[i.dataset.signal] = i.value.trim(); });
    try { await api('/priority-config', { method: 'PUT', body: c }); toast('Saved. Applies to newly triaged mail.', { kind: 'ok' }); } catch (e) { toast(e.message, { kind: 'error', ms: 6000 }); }
  });
}

// ---- Drafting -------------------------------------------------------------------------
async function drafting(pane) {
  const [d, st] = await Promise.all([api('/drafting'), api('/style')]);
  const wanted = (settings.DRAFT_FOR_PRIORITIES || '').split(',').map((x) => x.trim()).filter(Boolean);
  pane.innerHTML = `
    <div class="card pad">
      <h3>When to draft</h3>
      <p class="hint">A reply is drafted automatically when an email lands at one of these priorities, needs a reply, and the thread has not moved on. You can always ask for a draft from any email.</p>
      <div class="chips">${PRIORITIES.slice().reverse().map((p) => `<button class="chip ${wanted.includes(p) ? 'on' : ''}" data-dp="${p}" aria-pressed="${wanted.includes(p)}">${cap(p)}</button>`).join('')}</div>
      <input type="hidden" data-key="DRAFT_FOR_PRIORITIES" value="${esc(wanted.join(','))}">
      ${field('OPENAI_MODEL', 'Model', { hint: 'The OpenAI model that writes drafts. gpt-5.4-mini is fast and cheap; a bigger model is rarely worth it for short emails.' })}
      ${saveBar()}
    </div>

    <div class="card pad">
      <h3>Your writing style</h3>
      ${st.emails_analysed ? `<dl class="kv">
        <dt>Learned</dt><dd>${esc(new Date(st.learned_at).toLocaleDateString())} from ${st.emails_analysed} sent emails</dd>
        <dt>Greetings</dt><dd>${esc((st.greetings || []).join(' · '))}</dd>
        <dt>Sign-offs</dt><dd>${esc((st.signoffs || []).join(' · '))}</dd>
        <dt>Typical length</dt><dd>about ${st.median_words} words</dd>
        <dt>Signature</dt><dd>${st.signature_lines?.length ? `${st.signature_lines.length} lines detected` : 'none detected'}</dd>
      </dl>` : '<p class="hint">Not learned yet. The drafter needs this to sound like you.</p>'}
      <div class="row"><button class="btn" data-relearn>${icon('refresh')}${st.emails_analysed ? 'Re-learn from Sent Items' : 'Learn from Sent Items'}</button><span class="hint">Reads your recent sent mail. The profile stays on this PC.</span></div>
      <label class="field">Extra instructions for the drafter
        <textarea id="styleNotes" placeholder="e.g. Always call the partner team 'the RCVR team'. Never promise dates. Keep replies to external partners a little more formal.">${esc(st.extra_instructions || '')}</textarea>
        <span class="hint">Appended to the style brief on every draft. Plain sentences work best.</span></label>
      <div class="actions"><button class="btn primary" data-savenotes>Save instructions</button></div>
      ${(st.samples || []).length ? `<details class="adv"><summary>${st.samples.length} sample emails used as voice references</summary>${st.samples.map((x) => `<div class="sample">${esc(x)}</div>`).join('')}</details>` : ''}
    </div>

    <div class="card pad">
      <div class="row between"><h3>The prompt</h3><span class="hint">${d.is_default ? 'Using the default' : 'Customised'}</span></div>
      <p class="hint">This is the system prompt the drafting model receives. Placeholders in braces are filled in at draft time:</p>
      <div class="placeholder-list">${Object.entries(d.placeholders).map(([k, v]) => `<div><code>{${esc(k)}}</code> ${esc(v)}</div>`).join('')}</div>
      <label class="field">System prompt<textarea id="sysPrompt" class="code">${esc(d.system_prompt)}</textarea></label>
      <label class="field">Revision prompt <span class="hint">used when Jev's checks flag the first attempt; <code>{problems}</code> is the list of issues</span><textarea id="revPrompt" class="code" style="min-height:110px">${esc(d.revision_prompt)}</textarea></label>
      <label class="field">Phrases to avoid <span class="hint">one per line, case-insensitive. A draft containing any of these gets a revision pass.</span><textarea id="banned" class="code" style="min-height:160px">${esc(d.banned_phrases.join('\n'))}</textarea></label>
      <div class="actions">
        <button class="btn ghost" data-preview-prompt>${icon('eye')}Preview full prompt</button>
        <button class="btn ghost danger" data-resetprompt ${d.is_default ? 'disabled' : ''}>Reset to default</button>
        <button class="btn primary" data-saveprompt>Save prompt</button>
      </div>
      <pre class="preview hidden" id="promptPreview"></pre>
    </div>`;
  on(pane, 'click', '[data-dp]', (_, b) => { b.classList.toggle('on'); $('[data-key=DRAFT_FOR_PRIORITIES]', pane).value = $$('[data-dp].on', pane).map((x) => x.dataset.dp).join(','); $('[data-dirty]', pane)?.classList.remove('hidden'); });
  wireForm(pane);
  on(pane, 'click', '[data-relearn]', async (_, b) => { b.disabled = true; const t = toast('Reading your Sent Items', { ms: 60000 }); try { const r = await api('/style/learn', { method: 'POST' }); t.remove(); toast(`Learned from ${r.emails_analysed} emails`, { kind: 'ok' }); render(root, { sub: 'drafting', params: {} }); } catch (e) { t.remove(); toast(e.message, { kind: 'error', ms: 6000 }); b.disabled = false; } });
  on(pane, 'click', '[data-savenotes]', async () => { try { await api('/style/notes', { method: 'PUT', body: { extra_instructions: $('#styleNotes', pane).value } }); toast('Saved', { kind: 'ok' }); } catch (e) { toast(e.message, { kind: 'error' }); } });
  const promptBody = () => ({ system_prompt: $('#sysPrompt', pane).value, revision_prompt: $('#revPrompt', pane).value, banned_phrases: $('#banned', pane).value.split('\n').map((x) => x.trim()).filter(Boolean) });
  on(pane, 'click', '[data-saveprompt]', async () => { try { await api('/drafting', { method: 'PUT', body: promptBody() }); toast('Prompt saved. Next draft uses it.', { kind: 'ok' }); render(root, { sub: 'drafting', params: {} }); } catch (e) { toast(e.message, { kind: 'error', ms: 8000 }); } });
  on(pane, 'click', '[data-resetprompt]', async () => { if (!confirm('Reset the prompt, revision prompt and phrase list to the defaults?')) return; await api('/drafting/reset', { method: 'POST' }); toast('Reset to default'); render(root, { sub: 'drafting', params: {} }); });
  on(pane, 'click', '[data-preview-prompt]', async () => { const pre = $('#promptPreview', pane); pre.classList.remove('hidden'); pre.textContent = 'Building preview'; try { const r = await api('/drafting/preview'); pre.textContent = r.instructions; } catch (e) { pre.textContent = e.message; } });
}

// ---- Appearance ---------------------------------------------------------------------
async function appearance(pane) {
  const pref = themePref();
  pane.innerHTML = `
    <div class="card pad">
      <h3>Theme</h3>
      <div class="theme-options">
        ${[['system', 'Match system'], ['light', 'Light'], ['dark', 'Dark']].map(([v, l]) => `<button class="theme-option ${pref === v ? 'on' : ''}" data-theme="${v}"><span class="swatch ${v}" aria-hidden="true"><span></span><span></span></span>${l}</button>`).join('')}
      </div>
      <p class="hint">Saved in this browser. Press <kbd>t</kbd> anywhere to cycle.</p>
    </div>
    <div class="card pad">
      <h3>Keyboard</h3>
      <div class="kbd-list">
        <span><kbd>j</kbd> <kbd>k</kbd></span><span>Move between emails</span>
        <kbd>Enter</kbd><span>Open the selected email</span>
        <kbd>h</kbd><span>Mark done</span>
        <kbd>o</kbd><span>Open in Outlook</span>
        <kbd>Esc</kbd><span>Close the panel</span>
        <span><kbd>1</kbd> … <kbd>4</kbd></span><span>Today, Inbox, Drafts, Settings</span>
        <kbd>r</kbd><span>Check the inbox now</span>
        <kbd>t</kbd><span>Cycle the theme</span>
        <kbd>?</kbd><span>Show shortcuts</span>
      </div>
    </div>`;
  on(pane, 'click', '[data-theme]', (_, b) => { setTheme(b.dataset.theme); $$('[data-theme]', pane).forEach((x) => x.classList.toggle('on', x === b)); });
}
