# Email Triage

Sorts your Outlook inbox into **junk / low / medium / high / urgent**, tags each email with topics you define, and for urgent mail writes a reply draft in your own voice and drops it in your Outlook Drafts folder for review. Nothing is ever sent automatically.

- **Priority and tags** come from [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), TypeSafe's System One model. It returns typed answers with calibrated confidence instead of text, so one call per email (about 0.3 s, about $0.0001) answers "what priority, how time-sensitive, does it need a reply, which tags apply" all at once.
- **Reply drafts** come from an OpenAI model (default `gpt-5.4-mini`), briefed with a style profile learned from your Sent Items plus a list of AI-writing tells to avoid. Jev then checks each draft (does it sound like AI, does it answer the ask, does it invent facts, does it match your voice) and the model gets one revision pass if it fails.
- **Outlook categories** mirror the result: `Priority: Urgent` and your tag names appear as coloured categories on the email itself, so the triage is visible in Outlook, on your phone, everywhere.
- **Dashboard** at http://127.0.0.1:8765 to browse, correct priorities (corrections are fed back to Jev as examples), edit tags and priority definitions, and review drafts.

## Setup (Windows, classic Outlook)

Requirements: Windows, classic Outlook desktop signed in to your mailbox, Python 3.11 or newer.

1. Copy this folder somewhere (or `git clone` it).
2. Right-click `setup.ps1` and choose **Run with PowerShell** (or run `powershell -ExecutionPolicy Bypass -File setup.ps1`). It creates a virtual environment, installs dependencies, opens `.env` for your keys, checks the connections, and learns your writing style.
3. Put your keys in `.env`:
   - `TYPESAFE_API_KEY` from https://console.typesafe.ai/ (required)
   - `OPENAI_API_KEY` (optional; without it you get priorities and tags but no drafts)
4. Double-click `start.cmd`. The dashboard opens in your browser and the inbox watcher starts polling every two minutes.

Preview what Jev would decide without saving anything or touching Outlook:

```bash
.venv\Scripts\python -m emailtriage sample --hours 24
```

Backfill the last week:

```bash
.venv\Scripts\python -m emailtriage run --hours 168
```

Other commands: `check`, `learn-style`, `watch`, `serve --no-watch`.

### Not on classic Outlook?

The "new Outlook" app, Mac, and servers cannot use COM automation. Set `MAIL_BACKEND=graph` in `.env` and supply a `GRAPH_CLIENT_ID` from an Entra ID app registration (delegated permissions `Mail.ReadWrite`, `User.Read`; enable public client flows). Each user signs in once with a device code. This backend is written against the Graph v1.0 API but has not been exercised against a live tenant yet, so test it before relying on it.

## How the decision is made

For each email the tool sends Jev a JSON state (sender, whether they are a colleague, whether you are in To or only CC, subject, the newest message, a short excerpt of the earlier thread, and up to six of your recent corrections as examples) and asks, in one request:

| Question | Type | Used for |
|---|---|---|
| priority | choice over the five levels in `config/priority.json` | the headline priority |
| time_sensitivity | 4-level score | fallback rules, dashboard |
| needs_reply, action_requested, automated, external_business, escalation, recipient_only_copied | yes/no | fallback rules, dashboard |
| one yes/no per tag in `config/tags.json` | yes/no | tags |

Jev's `confidence` gates the answer. Above the threshold (default 0.5) the choice is used as is. Below it, the yes/no signals pick the priority and the email is flagged **needs review**. Two guardrails always apply: a shaky "urgent" is demoted to "high", and an automated notification can never be higher than "medium". The rules live in `emailtriage/jev.py` and the thresholds in `config/priority.json` (also editable in the dashboard).

## Editing tags and priorities

- **Dashboard > Tags**: add a tag with a name and a one-sentence description of when it applies. Jev evaluates it on every new email from then on. Use **Re-assess** on an email to apply new tags retroactively. Per-tag thresholds are optional.
- **Dashboard > Priorities**: the level descriptions are the literal instructions Jev receives. Rewrite them in your own words about your own inbox.
- **Correcting an email**: change its priority in the detail drawer. The correction is stored and the last few are included in every future Jev request as examples, so the model adapts to you over time.

Both files are plain JSON in `config/`; share them with colleagues if you want a common starting point.

## Drafts

When an email lands at a priority listed in `DRAFT_FOR_PRIORITIES` (default `urgent`) and is not an automated notification, a reply is drafted and saved into Outlook Drafts as a real reply to that email (quoted thread and your signature included). You get a Windows toast and the draft appears under **Dashboard > Drafts**. You can also draft on demand for any email from its detail view, optionally telling the drafter facts it should use.

The style profile (`config/style.json`) is learned from your Sent Items and kept on your PC. Re-learn any time from the Writing style page, and add free-text instructions there ("never promise dates", "keep partner emails a bit more formal").

The anti-AI-ese list in `emailtriage/drafter.py` is distilled from Wikipedia's [Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing): no em dashes, no "I hope this finds you well", no "not just X but Y", no tidy triads, no closing offers of further help, and so on. Edit `BANNED_PHRASES` if it fights with how you actually write.

## Privacy and compliance notes

- Email content is sent to TypeSafe (for triage) and, for drafts only, to OpenAI. Check both providers' data terms against your company policy before rolling out. The newest message is truncated to 6,000 characters and the earlier thread to 2,500.
- Everything else stays local: `data/triage.db` (SQLite) holds assessments and drafts, `config/style.json` holds sample sentences from your sent mail. Both are in `.gitignore`. Do not commit them.
- Nothing is sent, moved, or deleted in your mailbox. The tool only adds categories and creates drafts.

## Project layout

```
emailtriage/
  __main__.py      CLI (check, learn-style, sample, run, watch, serve)
  config.py        .env + JSON config loading
  jev.py           Jev state/question builder, confidence rules, draft checks
  drafter.py       OpenAI drafting, anti-AI lint, revision loop
  style.py         learn a voice profile from Sent Items
  pipeline.py      fetch -> assess -> categorise -> draft -> notify
  server.py        FastAPI JSON API for the dashboard
  static/index.html  the dashboard (vanilla JS, no build step)
  store.py         SQLite
  mail/            outlook_com.py (default), graph.py (alternative)
config/            priority.json, tags.json, style.json (generated)
tests/             pytest unit tests (python -m pytest)
```

## Ideas for the second stage

- Shared tag sets: keep `config/tags.json` in a shared repo so a team triages the same way.
- Daily digest: an "urgent + high since yesterday" summary email or Teams message.
- Sender memory: remember per-sender corrections ("everything from this vendor is low").
- Thread awareness: skip drafting when a colleague has already replied on the thread.
