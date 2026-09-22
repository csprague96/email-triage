# Email Triage

Sorts your Outlook inbox into **junk / low / medium / high / urgent**, tags each email with topics you define, and for urgent and high-priority mail writes a reply draft in your own voice and drops it in your Outlook Drafts folder for review. Nothing is ever sent automatically.

- **Priority and tags** come from [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), TypeSafe's System One model. It returns typed answers with calibrated confidence instead of text, so one call per email (about 0.3 s, about $0.0001) answers "what priority, how time-sensitive, does it need a reply, which tags apply" all at once.
- **Reply drafts** come from an OpenAI model (default `gpt-5.4-mini`), briefed with a style profile learned from your Sent Items plus a list of AI-writing tells to avoid. Jev then checks each draft (does it sound like AI, does it answer the ask, does it invent facts, does it match your voice) and the model gets one revision pass if it fails.
- **Outlook categories** mirror the result: `Priority: Urgent` and your tag names appear as coloured categories on the email itself, so the triage is visible in Outlook, on your phone, everywhere.
- **Dashboard** at http://127.0.0.1:8765: a Today view that shows only what needs you, an inbox grouped by priority, drafts, and every setting. Light and dark mode, keyboard shortcuts, works on a phone-sized window.

## Setup (Windows, classic Outlook)

Requirements: Windows, classic Outlook desktop signed in to your mailbox, Python 3.11 or newer.

1. Copy this folder somewhere (or `git clone` it).
2. Right-click `setup.ps1` and choose **Run with PowerShell** (or run `powershell -ExecutionPolicy Bypass -File setup.ps1`). It creates a virtual environment, installs dependencies, opens `.env` for your keys, checks the connections, and learns your writing style.
3. Put your keys in `.env` (or paste them later under **Settings > Account** in the dashboard):
   - `TYPESAFE_API_KEY` from https://console.typesafe.ai/ (required)
   - `OPENAI_API_KEY` (optional; without it you get priorities and tags but no drafts)
4. Run `install-background.ps1` the same way. It registers a Windows scheduled task that starts the watcher hidden at every logon, restarts it if it stops, and starts it right now. From then on mail is triaged every `POLL_SECONDS` (default 120) without you doing anything. Output goes to `data\watcher.log`.
5. Double-click `start.cmd` any time to open the dashboard. If the background job is not running it starts the watcher in a window instead.

To remove the background job run `uninstall-background.ps1`. Classic Outlook needs to be running for the watcher to read mail; the job will start it if it isn't.

Preview what Jev would decide without saving anything or touching Outlook:

```bash
.venv\Scripts\python -m emailtriage sample --hours 24
```

Backfill the last week:

```bash
.venv\Scripts\python -m emailtriage run --hours 168
```

Other commands: `check`, `login`, `learn-style`, `digest`, `sync-tags`, `watch`, `serve --no-watch`.

### Not on classic Outlook?

The "new Outlook" app, Mac, and servers cannot use COM automation. Switch to **Microsoft 365 sign-in** under Settings > Account (or set `MAIL_BACKEND=graph` in `.env`) and paste a `GRAPH_CLIENT_ID` from an Entra ID app registration (delegated permissions `Mail.ReadWrite`, `User.Read`; enable public client flows). The dashboard then shows a **Sign in with Microsoft** screen: open the link, enter the code, done. The token stays on your PC; sign out from the account menu. The same flow is available in a terminal with `python -m emailtriage login`. This backend is written against the Graph v1.0 API but has not been exercised against a live tenant yet, so test it before relying on it.

## The dashboard

`start.cmd` opens it, or go to http://127.0.0.1:8765. It is a plain static page served by the tool itself, no build step, no external scripts.

- **Today** is the screen to keep open. It lists urgent and high-priority emails from the last three days that you have not handled or replied to, most pressing first, one card each with the plain-language reasons Jev put it there ("Asks you to do something", "Today or tomorrow", "Sounds blocked"). Each card has one primary action, **Handled**, plus Open in Outlook, Draft reply and Details. Below it: drafts waiting in Outlook, low-confidence emails worth a look, and a collapsed "Later" list of medium-priority mail. Low and junk stay out of the way.
- **Inbox** is everything triaged, grouped by priority with low and junk collapsed. Search, filter by tag or time range, show only the emails Jev was unsure about, or include handled ones.
- **Drafts** shows every reply draft with Jev's check results, and buttons to open it in Outlook, mark it done or discard it.
- Clicking any email opens a **detail panel**: why it landed where it did, the priority as a one-click control (changing it teaches Jev), tags to toggle, the thread summary, the message, drafts, and Jev's raw numbers under a fold.
- **Settings** covers everything that used to require editing `.env`: account and mail source, API keys (write-only, never displayed again), how often to check, categories and notifications, the Slack digest, tags and the shared team tag source, priority definitions and thresholds, the drafting prompt, and the theme. Saving writes to `.env` and applies on the next inbox check without a restart.

Shortcuts: `j` / `k` move between emails, `Enter` opens, `h` marks handled, `o` opens in Outlook, `Esc` closes, `1` to `4` switch pages, `r` checks the inbox, `t` cycles the theme, `?` shows the list. The theme follows your system by default and is remembered per browser.

## How the decision is made

For each email the tool sends Jev a JSON state (sender, whether they are a colleague, whether you are in To or only CC, subject, the newest message, a short excerpt of the earlier thread, and up to six of your recent corrections as examples) and asks, in one request:

| Question | Type | Used for |
|---|---|---|
| priority | choice over the five levels in `config/priority.json` | the headline priority |
| time_sensitivity | 4-level score | fallback rules, dashboard |
| needs_reply, action_requested, automated, external_business, escalation, recipient_only_copied | yes/no | fallback rules, dashboard |
| one yes/no per tag in `config/tags.json` | yes/no | tags |

Jev's `confidence` gates the answer. Above the threshold (default 0.5) the choice is used as is. Below it, the yes/no signals pick the priority and the email is flagged **unsure**. Two guardrails always apply: a shaky "urgent" is demoted to "high", and an automated notification can never be higher than "medium". The rules live in `emailtriage/jev.py` and the thresholds in `config/priority.json` (also editable under Settings > Priorities).

## Editing tags and priorities

- **Settings > Tags**: add a tag with a name and a one-sentence description of when it applies. Jev evaluates it on every new email from then on. Use **Re-assess** on an email to apply new tags retroactively. Per-tag thresholds are optional.
- **Settings > Priorities**: the level descriptions are the literal instructions Jev receives. Rewrite them in your own words about your own inbox.
- **Correcting an email**: change its priority in the detail panel. The correction is stored and the last few are included in every future Jev request as examples, so the model adapts to you over time.

Both files are plain JSON in `config/`; share them with colleagues if you want a common starting point.

## Drafts

When an email lands at a priority listed under Settings > Drafting (default urgent and high) and is not an automated notification, a reply is drafted and saved into Outlook Drafts as a real reply to that email (quoted thread and your signature included). You get a Windows toast and the draft appears under **Drafts**. You can also draft on demand for any email from its detail panel, optionally telling the drafter facts it should use.

The style profile (`config/style.json`) is learned from your Sent Items and kept on your PC. Re-learn any time from Settings > Drafting, and add free-text instructions there ("never promise dates", "keep partner emails a bit more formal").

**The prompt itself is editable.** Settings > Drafting shows the system prompt the model receives, with placeholders for your name, the style brief, your sample emails and the banned phrase list, plus the revision prompt used when Jev's checks fail and the list of phrases to avoid. "Show what the model sees" renders the prompt exactly as it will be sent. Your edits live in `config/drafting.json`; Reset to default removes the file. The default phrase list is distilled from Wikipedia's [Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing): no em dashes, no "I hope this finds you well", no "not just X but Y", no tidy triads, no closing offers of further help, and so on.

## Thread awareness

Before drafting, the tool reads the whole conversation (Inbox plus Sent Items):

- If you already replied after this message, no draft is written and the priority drops to low. The email also leaves the Today view.
- If a colleague replied and you were only copied, no draft and the priority is capped at medium.
- If a newer message is already on the thread, no draft for the older one; the newer one is triaged on its own.
- A new message on a thread marks any unsent draft for an earlier message as superseded.
- Once you reply on a thread, its waiting draft is marked done automatically on the next inbox check.

The detail panel shows the thread summary for each email.

## Shared tags

Set a shared tags source under Settings > Tags (or `SHARED_TAGS_SOURCE` in `.env`): an https URL or a file/UNC path of a `tags.json` (same shape as `config/tags.json`). A good choice is the raw URL of the file in your team's repo, so editing it there updates everyone. Shared tags are merged with each person's local tags, shown read-only, and re-fetched every `SHARED_TAGS_REFRESH_HOURS` (default 6) or with **Sync now**. Local tags with the same name as a shared one are ignored.

## Daily digest to Slack

Create an Incoming Webhook (https://api.slack.com/messaging/webhooks) for the channel or DM you want, and paste it under Settings > Digest & Slack. While the watcher is running, a digest is posted at the chosen time (local, default 08:00, weekdays only unless you untick it) covering the chosen lookback window: counts by priority, the urgent and high items, drafts waiting in Outlook, and low-confidence items worth a look. Only subjects, sender names and tags are sent, never bodies. Preview it, send it now, or pause it with the toggle without removing the webhook. From a terminal:

```bash
.venv\Scripts\python -m emailtriage digest --send
```

## Privacy and compliance notes

- Email content is sent to TypeSafe (for triage) and, for drafts only, to OpenAI. The Slack digest carries subjects and sender names only. Check both providers' data terms against your company policy before rolling out. The newest message is truncated to 6,000 characters and the earlier thread to 2,500.
- Everything else stays local: `data/triage.db` (SQLite) holds assessments, drafts and your handled marks, `config/style.json` holds sample sentences from your sent mail, `.env` holds your keys. All are in `.gitignore`. Do not commit them.
- The dashboard listens on 127.0.0.1 only. Settings and other changes require a header that web pages on other sites cannot send, so a stray browser tab cannot change your configuration or post a digest. API keys are write-only in the UI.
- Nothing is sent, moved, or deleted in your mailbox. The tool only adds categories and creates drafts.

## Project layout

```
emailtriage/
  __main__.py      CLI (check, login, learn-style, sample, run, watch, serve)
  config.py        .env + JSON config loading, settings schema and .env writer
  prompts.py       default drafting prompt and banned phrase list
  jev.py           Jev state/question builder, confidence rules, draft checks
  drafter.py       OpenAI drafting, anti-AI lint, revision loop
  style.py         learn a voice profile from Sent Items
  pipeline.py      fetch -> thread check -> assess -> categorise -> draft -> notify
  digest.py        daily Slack digest
  shared_tags.py   team tag set sync
  server.py        FastAPI JSON API for the dashboard
  static/          the dashboard (ES modules, no build step)
    app.js           shell, routing, theme, sign-in, shortcuts
    views/           today, inbox, drafts, settings
    detail.js        the email panel
  store.py         SQLite
  mail/            outlook_com.py (default), graph.py (Microsoft 365 sign-in)
config/            priority.json, tags.json, style.json and drafting.json (generated)
tests/             pytest unit tests (python -m pytest)
```
