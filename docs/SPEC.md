# Sundial — Technical Specification

**Status:** Draft v0.1
**Owner:** Joe McMahon
**Last updated:** 2026-08-19

---

## 1. What Sundial is

Sundial is a single-user day-management application: one screen that merges a
calendar, a todo list, and an email-derived task inbox, and then does the thing
none of those three do on their own — it decides *when* you are actually going
to do the work, and defends that decision on your real calendar.

It is deployed to AWS, used from a browser on desktop and phone, and reads and
writes Google Calendar and Gmail.

### 1.1 The four jobs

| # | Job | What it means concretely |
|---|-----|--------------------------|
| J1 | **Unified view** | One fast, uncluttered screen: today's events, today's blocks, what's due, what slipped. Never three apps. |
| J2 | **Auto time-blocking** | Sundial reads your commitments, finds the gaps, and schedules todos into them by due date, priority, and estimated effort. Blocks become real Google Calendar events. |
| J3 | **Email → tasks** | Gmail is scanned for messages that imply an obligation. Sundial proposes tasks; you accept or dismiss. Nothing is created silently. |
| J4 | **Daily brief** | A morning digest: what's on, what's due, what slipped from yesterday, and where the plan is over-committed. |

J1 is the floor. If J2–J4 are switched off, Sundial must still be a genuinely
good calendar + todo app. Every automated feature is a layer on top of a
product that works without it.

### 1.2 Non-goals

- Multi-user, teams, sharing, or collaboration. Single user, one Google account.
- Being a Google Calendar client. Sundial does not attempt to render every
  Google feature (working locations, appointment schedules, out-of-office).
- Email *client* functionality. Sundial reads Gmail to extract tasks; it does
  not compose, thread, or send mail on your behalf.
- Offline write support in v1 (offline *read* is supported — see §10.4).
- Native iOS/Android apps. A PWA covers it.

---

## 2. Locked decisions

These came out of the design conversation and are the premises the rest of the
spec is built on.

| ID | Decision | Consequence |
|----|----------|-------------|
| D1 | **Sundial is the source of truth** for everything it creates. | Sundial owns a first-class event store; Google Calendar receives a mirror. |
| D2 | **Foreign events are imported read-only.** | Events created outside Sundial (invites, meetings others booked) are pulled in as immovable constraints, never edited by Sundial. Google wins on any conflict for these. |
| D3 | **Time blocks are real Google Calendar events**, written to a dedicated `Sundial` calendar. | Blocks appear on your phone, mark you busy to others, and fire Google's own alerts. Sundial may rewrite them freely. |
| D4 | **LLM (Claude) does the inference.** | Email triage, natural-language task entry, and effort estimation use Claude. The scheduling algorithm itself stays deterministic. |
| D5 | **Reminders ride three channels:** web push, email (SES), and Google Calendar's native alerts. | No SMS, no per-message cost. |
| D6 | **Single user, minimal cost.** | Serverless, scale-to-zero. Target under $10/month all-in. |
| D7 | **Python backend, React front end.** | Lambda handlers in Python 3.13; React + Vite PWA; AWS CDK in Python. |

### 2.1 The one place D1 and D2 have to be reconciled

Declaring "Sundial is source of truth" while also accepting invites in Gmail
means the system is *de facto* bidirectional. D2 is what keeps that from
becoming a full conflict-resolution engine: authority is decided **per event,
once, at creation time**, and never changes.

```
                    who created it?
                          |
        +-----------------+------------------+
        |                                    |
   Sundial (task blocks,              Google / anyone else
   events you type into                (invites, other people's
   Sundial)                             meetings, other apps)
        |                                    |
  origin = "sundial"                  origin = "google"
  Sundial is authoritative            Google is authoritative
  writes flow  S ---> G               writes flow  S <--- G
  Google-side edits are               Sundial UI shows them
  DETECTED and REVERTED               read-only; they act as
  (or surfaced, see §6.5)             hard scheduling constraints
```

There is exactly one writer per event. No merge, no last-write-wins, no
vector clocks. This is the single most important simplification in the design.

---

## 3. Domain model

### 3.1 Entities

**Task** — a thing to be done. The core object.

| Field | Type | Notes |
|-------|------|-------|
| `task_id` | ULID | |
| `title` | string | |
| `notes` | string | markdown |
| `status` | enum | `inbox` / `active` / `done` / `dropped` |
| `priority` | enum | `P1`..`P4` |
| `due_at` | timestamp\|null | a real deadline, not a wish |
| `defer_until` | timestamp\|null | hidden from lists before this |
| `estimate_minutes` | int | LLM-suggested, user-editable, default 30 |
| `min_chunk_minutes` | int | smallest useful working slice, default 25 |
| `splittable` | bool | may be scheduled across multiple blocks |
| `energy` | enum | `deep` / `shallow` / `admin` — gates which windows it can occupy |
| `project_id` | ULID\|null | |
| `source` | enum | `manual` / `email` / `recurring` |
| `source_ref` | string\|null | Gmail message id, for `email` |
| `pinned_block_id` | ULID\|null | user hand-placed it; scheduler must not move it |
| `completed_at` | timestamp\|null | |

**Event** — something occupying time.

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | ULID | Sundial's id |
| `origin` | enum | `sundial` / `google` — **immutable after creation** |
| `kind` | enum | `appointment` / `block` / `busy` |
| `google_event_id` | string\|null | |
| `google_calendar_id` | string | |
| `google_etag` | string\|null | last etag Sundial saw |
| `title`, `start`, `end`, `all_day`, `location` | | |
| `tz` | IANA string | events store the zone they were authored in |
| `task_id` | ULID\|null | set when `kind = block` |
| `transparency` | enum | `busy` / `free` |
| `locked` | bool | user said "don't move this" |
| `deleted_at` | timestamp\|null | soft delete, kept for sync reconciliation |

**Project** — grouping, with an optional weekly time target.

**Reminder** — `(target_id, target_type, fire_at, channels[], state)`.

**EmailCandidate** — a proposed task awaiting your verdict:
`(gmail_message_id, thread_id, from, subject, snippet, proposed_task{...}, confidence, state: pending/accepted/dismissed)`.
Dismissals are remembered so the same message is never re-proposed.

**SchedulingPolicy** — one singleton document:
working hours per weekday, deep-work windows, lunch, no-schedule-before /
after, max blocks per day, min gap between blocks, buffer around meetings,
per-energy allowed windows, "protect Fridays after 15:00", etc.

### 3.2 Storage: DynamoDB single table

One table, `sundial`, on-demand billing. Single user means the partition key
is coarse by design — item counts are in the thousands, not millions.

| Entity | PK | SK | Notes |
|--------|----|----|-------|
| Task | `USER#<uid>` | `TASK#<task_id>` | |
| Event | `USER#<uid>` | `EVENT#<iso_start>#<event_id>` | SK sorts by time; range queries for a day/week are a single `Query` |
| Project | `USER#<uid>` | `PROJ#<project_id>` | |
| EmailCandidate | `USER#<uid>` | `EMAIL#<gmail_message_id>` | natural dedupe key |
| Policy | `USER#<uid>` | `POLICY#v1` | |
| SyncState | `USER#<uid>` | `SYNC#<resource>` | Google sync tokens, Gmail historyId, watch channel expiry |
| Reminder | `USER#<uid>` | `REM#<fire_at>#<reminder_id>` | |
| OAuth tokens | `USER#<uid>` | `AUTH#google` | refresh token encrypted with KMS (§11.1) |

**GSI1 — lookup by Google id.** `GSI1PK = GEVT#<google_event_id>`,
`GSI1SK = <event_id>`. Needed on every inbound sync page to answer "do I
already know this event?"

**GSI2 — task work queues.** `GSI2PK = STATUS#<status>`,
`GSI2SK = <due_at or 9999>#<priority>`. Drives the scheduler's candidate list
and the "what's due" views without a table scan.

DynamoDB Streams on the table feed the reminder materializer and the search
index refresh. PITR is on.

---

## 4. Architecture

```
   Phone / Desktop browser
            |
            | HTTPS
            v
   CloudFront ──────────────► S3 (React PWA build, immutable assets)
        │
        │ /api/*  (same origin, so session cookies are first-party)
        ▼
   API Gateway (HTTP API)
        │  Lambda authorizer → validates Sundial session JWT
        ▼
   ┌───────────────────────── Lambda (Python 3.13) ─────────────────────────┐
   │  api          — CRUD, views, task/event/policy endpoints               │
   │  oauth        — Google authorization-code flow, token refresh          │
   │  sync_cal     — Google Calendar incremental sync (pull + push)         │
   │  sync_gmail   — Gmail history scan → EmailCandidates                   │
   │  scheduler    — time-blocking solver                                   │
   │  reminders    — fire web push / SES email                              │
   │  brief        — compose + deliver the daily brief                      │
   │  llm          — Claude calls: triage, NL parse, estimates              │
   └────────────────────────────────────────────────────────────────────────┘
        │            │                │              │
        ▼            ▼                ▼              ▼
   DynamoDB      Secrets Mgr     EventBridge      SQS (+ DLQ)
   (+ Streams)   (+ KMS CMK)     Scheduler        work queue
                                      │
                                      ▼
                              SES  ·  Web Push (VAPID)
        │
        ▼
   Google APIs (Calendar v3, Gmail v1)  ·  Anthropic API (Claude)
```

### 4.1 Why each piece

- **CloudFront + S3** — static PWA, free tier covers it, and serving the API
  under the same origin (`/api/*` behavior with the API Gateway as a second
  origin) means the session cookie is first-party. That sidesteps Safari's
  third-party cookie behavior on iOS entirely, which matters because the phone
  is a primary client.
- **API Gateway HTTP API**, not REST API — a third of the cost, and none of
  the features REST API adds are needed here.
- **Lambda, Python 3.13**, ARM64/Graviton. One function per bounded
  responsibility, not one per endpoint: the `api` function is a single handler
  with an internal router (FastAPI + Mangum), which keeps cold starts to one
  warm path for interactive traffic.
- **DynamoDB on-demand** — at single-user volume this is cents. No connection
  pooling problem from Lambda, unlike RDS.
- **EventBridge Scheduler** (not CloudWatch Events rules) — supports one-shot
  schedules with arbitrary future timestamps, which is exactly a reminder.
  1M invocations/month free.
- **SQS between sync and scheduler** — Google webhooks are at-least-once and
  bursty; the queue absorbs that and gives retries plus a DLQ for free.
- **Secrets Manager + a customer-managed KMS key** for the Google refresh
  token and the Anthropic API key.
- **No VPC.** Nothing here needs one, and putting Lambda in a VPC would mean
  NAT Gateway at ~$32/month — three times the rest of the bill combined.

### 4.2 Cost estimate (single user, monthly)

| Service | Estimate |
|---------|----------|
| Lambda | $0 (free tier) |
| API Gateway HTTP API | ~$0.05 |
| DynamoDB on-demand + PITR | ~$0.30 |
| S3 + CloudFront | ~$0.50 |
| Route 53 hosted zone | $0.50 |
| Secrets Manager (2 secrets) | $0.80 |
| KMS CMK | $1.00 |
| SES | ~$0.00 (under 1k mails) |
| EventBridge Scheduler | $0 (free tier) |
| CloudWatch logs (7-day retention) | ~$0.50 |
| Claude API (Claude Opus 5) | ~$3–9 (see §8.4) |
| **Total** | **~$7–13/month** |

Domain registration is separate and annual.

---

## 5. Authentication and Google access

### 5.1 Sundial's own auth

Single user, so this stays small:

1. Browser hits `/api/auth/login` → redirect to Google's consent screen
   (authorization code flow with PKCE, `access_type=offline`, `prompt=consent`).
2. Callback lands on the `oauth` Lambda. Sundial checks the returned Google
   account id against a single-entry allowlist in config. Anyone else is
   rejected outright — no user record is created.
3. Sundial mints its own session JWT (RS256, 30-day expiry, signing key in
   Secrets Manager), sets it as `HttpOnly; Secure; SameSite=Lax` on the
   apex domain. Because the API is served under the same origin via
   CloudFront, this is a first-party cookie and survives on iOS Safari.
4. A Lambda authorizer validates the JWT on every `/api/*` call and caches
   the result for 300s.

Cognito is deliberately not used. It buys user-pool management this app has
no use for, and the Google refresh token still has to be handled separately
because Sundial needs `offline_access` with its own scope set. Revisit only
if D6 changes.

### 5.2 Google scopes requested

| Scope | Why | Sensitivity |
|-------|-----|-------------|
| `openid`, `email`, `profile` | identify the account | basic |
| `https://www.googleapis.com/auth/calendar.events` | read all events, write to the Sundial calendar | sensitive |
| `https://www.googleapis.com/auth/calendar.calendarlist.readonly` | discover which calendars exist | sensitive |
| `https://www.googleapis.com/auth/gmail.readonly` | J3 triage | **restricted** |
| `https://www.googleapis.com/auth/gmail.modify` | *optional*, only to apply a `Sundial/Tracked` label | **restricted** |

Sundial requests `gmail.readonly` and treats `gmail.modify` as opt-in at
first run. If labelling is not wanted, `gmail.modify` is never requested.

### 5.3 ⚠️ The OAuth verification problem — decision required

This is the single biggest external constraint on the project, and it is
worth settling before any code is written.

Google gates apps by publishing status and scope sensitivity:

- **Testing status.** You add yourself as a test user and it works
  immediately with any scope — but **refresh tokens issued to an app in
  testing expire after 7 days.** In practice that means re-consenting every
  week, forever. Sundial would be doing background sync, so this shows up as
  a silently dead sync every Monday.
- **Production status.** Refresh tokens are long-lived, but publishing an
  app that requests *restricted* scopes (which both Gmail scopes are)
  requires OAuth verification plus an independent CASA security assessment.
  That is a real process with real cost and a multi-week turnaround, and it
  is aimed at products, not personal tools.
- **Internal (Workspace only).** If the Google account is on a Google
  Workspace domain you control, the app can be marked **Internal**. No
  verification, no CASA, no 7-day token expiry, all scopes available.

**Recommendation, in order of preference:**

1. **Use a Google Workspace account.** If you have one, or are willing to
   pay for the cheapest tier on a domain you already own, mark the app
   Internal and the whole problem disappears. This is by far the cleanest
   path and the spec assumes it unless told otherwise.
2. **Ship J1/J2/J4 first on Calendar scopes only.** Calendar scopes are
   *sensitive*, not *restricted* — verification is lighter and CASA is not
   required. Gmail (J3) becomes a phase-2 feature gated behind whichever
   path you choose here.
3. **Accept weekly re-auth.** Sundial detects `invalid_grant`, stops
   syncing, and pushes you a "reconnect Google" notification. Workable, but
   annoying by design.

Everything else in this spec is unaffected by which option you pick; only
the deployment runbook and the phase order change.

### 5.4 Token handling

The refresh token is encrypted with a customer-managed KMS key and stored in
the `AUTH#google` item. Access tokens are cached in memory per Lambda
execution environment and never persisted. On `invalid_grant`, Sundial marks
the connection dead, halts all sync loops, and raises a reconnect prompt in
the UI plus a push notification. It does not retry blindly.

---

## 6. Calendar sync engine

### 6.1 Calendars

Sundial creates and owns one Google calendar, `Sundial`, at first run and
records its id. **Everything Sundial writes goes there and nowhere else.**
This is what makes D1/D2 safe: an entire class of "did Sundial just mangle
my work calendar" bugs is impossible, and nuking the integration is a
one-click calendar delete.

All other calendars in your `calendarList` are read-only sources of busy
time. The policy document says which of them count as blocking.

### 6.2 Pull: incremental sync

Per calendar, Sundial keeps a `syncToken` in the `SYNC#<calendar_id>` item.

```
sync_cal(calendar_id):
  token = load_sync_token(calendar_id)
  loop over events.list(calendarId, syncToken=token, showDeleted=True, pages):
      for each google event:
          existing = GSI1.get(GEVT#<google_event_id>)
          if existing is None:
              if calendar_id == sundial_calendar: ORPHAN  → see §6.5
              else: create Event(origin="google", kind="busy"|"appointment")
          elif existing.origin == "google":
              overwrite local fields from Google      # Google wins, D2
          elif existing.origin == "sundial":
              if google.etag != existing.google_etag: DRIFT → see §6.5
  store new syncToken
  enqueue RESCHEDULE if any blocking event changed
```

A `410 Gone` on the sync token means the token expired; Sundial falls back
to a full list for a bounded window (−30 days to +180 days) and rebuilds.
Full resync is idempotent because `google_event_id` is the dedupe key.

### 6.3 Push: staying fresh without polling

`events.watch` registers a webhook channel per calendar pointing at
`POST /api/google/webhook`. Google's notifications carry no payload — they
just say "something changed on this resource" — so the handler validates the
channel token, drops a message on SQS, and returns 200 immediately. The
`sync_cal` worker then does an incremental sync. Channels expire (max ~30
days for calendar); a daily EventBridge rule renews any channel expiring
within 48 hours.

A safety-net EventBridge rule runs a full incremental sync every 30 minutes
regardless, so a missed webhook costs at most half an hour of staleness.

### 6.4 Write path

Sundial writes to Google **after** committing to DynamoDB, via the SQS queue,
so the UI never waits on Google and a Google outage degrades to "your plan is
correct in Sundial and will appear on your phone shortly".

Every Sundial-written event carries extended properties:

```
extendedProperties.private = {
  "sundial_event_id": "<ulid>",
  "sundial_task_id":  "<ulid or ''>",
  "sundial_kind":     "block" | "appointment",
  "sundial_rev":      "<int>"
}
```

These are invisible in the Google UI, survive round-trips, and let Sundial
recognise its own events even if the local database were rebuilt from
scratch. A block's `colorId` is set per project so the phone calendar is
readable at a glance.

Writes are idempotent: the Google `eventId` is derived deterministically from
`sundial_event_id` (base32hex-encoded, which satisfies Google's id charset
rules), so a retried create is a no-op rather than a duplicate.

### 6.5 Conflict cases and what happens

| Case | Rule |
|------|------|
| Foreign event edited in Google | Google wins. Local copy overwritten. Reschedule triggered if it blocks time. |
| Foreign event deleted in Google | Soft-deleted locally. Freed time is reclaimed on next schedule. |
| **Sundial block dragged in Google Calendar** | Treated as a *user intent signal*, not an error: the block is marked `locked=true`, the new time is adopted locally, and the scheduler works around it. Silently reverting a deliberate drag would be the most infuriating possible behaviour. |
| **Sundial block deleted in Google** | The task is unscheduled and returned to the queue with a `skipped_today` flag. Not re-created on the same day. |
| Sundial *appointment* edited in Google | Local value wins and is pushed back, with a UI notice. These are events you explicitly authored in Sundial. |
| Orphan Sundial-calendar event with no local record | Deleted from Google (it is by definition stale) unless created in the last 5 minutes — that window protects against a race with an in-flight write. |
| Same event on two calendars (invite + personal copy) | Deduped by `iCalUID`; the copy on the primary calendar wins. |

### 6.6 Recurrence

v1 does **not** expand recurrence itself. Sundial asks Google for
`singleEvents=true` over the sync window and stores the instances, which
means recurring meetings are correctly treated as busy time without Sundial
owning an RRULE engine. Sundial-authored *recurring* events are out of scope
for v1; recurring **tasks** are handled in the task layer (a completed
recurring task spawns the next instance), which is where the value actually
is.

### 6.7 Time zones

Every timestamp is stored UTC. Every event also stores the IANA zone it was
authored in. The scheduler always reasons in the user's *current* zone,
which is read from the browser and stored on the policy document, so
travelling changes the working-hours window without rewriting history. All-day
events are stored as date-only and never converted.

---

## 7. The scheduler (J2)

The heart of the product. It is deliberately **deterministic** — no LLM in
this loop. Claude estimates how long a task takes (§8.3); the placement of
that estimate on a timeline is arithmetic, and it must be reproducible,
explainable, and instant.

### 7.1 Inputs

- All blocking events in the horizon (today + 13 days), from any calendar
  marked as blocking.
- All tasks with `status = active`, `defer_until` passed.
- The `SchedulingPolicy`.
- Existing blocks — including `locked` ones and anything already started.

### 7.2 Algorithm

**Step 1 — build free/busy.** Merge all blocking intervals, subtract from
the policy's working windows, subtract meeting buffers, split into candidate
slots. Discard slots shorter than the global `min_chunk_minutes`.

**Step 2 — score and sort tasks.**

```
urgency   = f(due_at)      # steep ramp inside 48h, flat beyond a week
priority  = weight[P1..P4]
age       = small bonus per day since creation, capped  # anti-starvation
score     = w_u*urgency + w_p*priority + w_a*age + w_project_debt
```

`w_project_debt` rises when a project with a weekly time target is behind
pace, which stops a single loud deadline from starving everything else for
a fortnight.

**Step 3 — greedy placement.** Highest score first, earliest feasible slot
that satisfies: energy window match (`deep` work only in deep windows),
`min_chunk_minutes`, max blocks/day, min gap between blocks, and
`due_at` (never scheduled after its deadline; if that is impossible the task
is flagged, not silently placed late).

**Step 4 — local repair.** A bounded improvement pass (max 200 swap/shift
moves, hard 3-second cap) that reduces fragmentation, pulls deadline-risk
tasks earlier, and clusters same-project work. Any move that would place a
task after its due date, or move a `locked`/`pinned` block, is rejected.

**Step 5 — diff and commit.** The new schedule is diffed against the current
one. Only real changes are written; identical blocks are left completely
alone so Google isn't churned and your phone doesn't buzz for nothing.

### 7.3 Stability rules

Users abandon auto-schedulers that reshuffle everything constantly. So:

- **The current and next block never move**, unless a hard conflict appears.
- **Blocks in the past never move.** Ever.
- A block within 30 minutes of starting is frozen.
- `locked` blocks are immovable input, not output.
- Rescheduling is debounced: triggers coalesce over a 60-second window.
- If the diff exceeds a threshold (more than 40% of remaining blocks), the
  new plan is presented as a **proposal** in the UI rather than applied.

### 7.4 Triggers

Task created/edited/completed · blocking event added/changed/removed ·
policy edited · daily at the start of the working window · manual "replan".

### 7.5 Overcommitment

When the queue does not fit in the horizon, Sundial does not quietly drop
work. It surfaces: *"You're 6h 20m over capacity this week. 3 tasks can't
make their deadlines."* with the specific tasks and three offered actions —
extend hours, drop, or defer. Honest failure beats a plausible-looking
schedule you can't actually execute.

---

## 8. Gmail → tasks (J3) and the LLM layer

### 8.1 Ingest

An EventBridge rule runs `sync_gmail` every 15 minutes during working hours.
It uses `users.history.list` from the stored `historyId` — cheap and
incremental — falling back to `users.messages.list` with a bounded query on
first run or `404` (expired history):

```
newer_than:7d -in:chats -category:promotions -category:social -from:me
```

Gmail push via Cloud Pub/Sub is *possible* but drags a whole GCP Pub/Sub
topic, subscription, and push endpoint into the design for a feature that
tolerates 15-minute latency. Not worth it; noted as a future option.

### 8.2 Triage

Candidates are pre-filtered with cheap deterministic rules before any token
is spent — messages already dismissed, mailing lists (`List-Unsubscribe`
header), automated senders (`no-reply@`), and threads where you sent the
last message are dropped locally.

Survivors go to Claude in batches of up to 10, with **only** subject, sender,
date, and the first ~1500 characters of the plain-text body. Full bodies,
attachments, and images are never sent.

The call uses structured outputs so the response is schema-valid by
construction, with adaptive thinking on and effort dialled down since this is
a routine classification:

```python
resp = client.messages.create(
    model="claude-opus-5",
    max_tokens=4096,
    thinking={"type": "adaptive"},
    output_config={
        "effort": "low",
        "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA},
    },
    system=[{"type": "text", "text": TRIAGE_SYSTEM,
             "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": render_batch(messages)}],
)
```

The system prompt is stable and cached, so the per-call cost is dominated by
the message batch itself. Per message Claude returns:
`is_actionable`, `confidence`, `title`, `notes`, `due_at`, `estimate_minutes`,
`energy`, `priority`, `reasoning`.

Anything at or above the confidence threshold becomes an `EmailCandidate` in
the **Inbox** tray. **Nothing is auto-accepted into the schedule.** You
accept, edit-then-accept, or dismiss; dismissals are permanent per message
and are fed back as few-shot negatives to sharpen future runs.

### 8.3 Other LLM uses

- **Natural-language capture.** "call the dentist next tues arvo, 15 min" →
  a fully-formed task. Same structured-output pattern, one message.
- **Effort estimation.** When a task is created without an estimate, Claude
  proposes one, primed with your own history of similar completed tasks
  (title + actual time). Always editable; the number shown is a suggestion,
  never a silent default.
- **Daily brief prose.** The brief's data is assembled deterministically;
  Claude writes the two-sentence summary at the top.

Claude is never given write access to the database. Every LLM call returns
structured data that Sundial's own code validates and applies.

### 8.4 Model choice and cost

Default model is **Claude Opus 5** (`claude-opus-5`, $5/MTok input,
$25/MTok output) for every call. At a realistic volume — ~60 triaged
messages/day at ~400 input tokens each, plus a handful of NL captures and
one brief — that lands around **$5–9/month**, with prompt caching on the
system prompt already accounted for.

Two levers if that's more than you want to spend, both purely configuration:

- Route triage (a simple classification) to **Claude Haiku 4.5**
  (`claude-haiku-4-5`, $1/$5 per MTok) and keep Opus 5 for NL capture and
  the brief. Drops the LLM line to roughly $1–2/month.
- Raise the deterministic pre-filter's aggressiveness so fewer messages
  reach the model at all.

The model id is a config value per call-site (`TRIAGE_MODEL`, `PARSE_MODEL`,
`BRIEF_MODEL`), so this is a one-line change, not a refactor.

### 8.5 Guardrails

Email content is untrusted input. A message body can contain text that looks
like an instruction. Mitigations:

- Message content is passed as data inside a delimited user block; the system
  prompt states explicitly that message text is data to classify, never
  instructions to follow.
- The response is schema-constrained — there is no channel through which a
  message could cause anything other than a proposed task to appear.
- The LLM has no tools and no write path.
- Every proposal is human-approved before it affects the schedule.

---

## 9. Reminders and the daily brief

### 9.1 Three channels, three jobs

| Channel | Best at | Mechanism |
|---------|---------|-----------|
| **Google Calendar alerts** | Everything already on the calendar — meetings and time blocks | Sundial sets `reminders.overrides` on the events it writes. Zero Sundial infra; fires natively on your phone even if Sundial is down. |
| **Web push** | Task-level nudges with no calendar event: due-soon, deferred-task-now-active, "your day is overcommitted" | VAPID web push to a PWA service worker. Subscription stored per device; one row per browser. |
| **Email (SES)** | The daily brief, weekly review, and a fallback when a push fails | SES in sandbox is fine (verified sender + verified recipient = you). |

Reminder items are materialised into `REM#<fire_at>#<id>` rows. A DynamoDB
Stream handler creates a **one-shot EventBridge Scheduler schedule** per
reminder, which invokes the `reminders` Lambda at the exact minute. Editing
or completing the task cancels the schedule. This costs nothing at rest and
avoids the classic "poll every minute" Lambda.

iOS caveat, stated plainly: web push on iOS requires the PWA to be added to
the Home Screen (iOS 16.4+). If it isn't, push silently doesn't arrive — so
first-run detects the standalone-display state and, if not installed, walks
you through Add to Home Screen before offering push. Google Calendar alerts
and email cover the gap either way.

### 9.2 Daily brief

Runs on a schedule (default 06:30 local, configurable). Contents:

- One-paragraph summary of the shape of the day (Claude-written).
- Timeline: meetings and blocks in order, with total committed hours.
- Due today / overdue.
- Slipped: yesterday's incomplete blocks, with one-tap reschedule.
- Capacity warning if the week is overcommitted.
- New email candidates awaiting triage, with accept/dismiss links.

Delivered as an SES email plus an in-app view at `/brief`. A shorter
**weekly review** on Sunday evening covers completion rate, estimate accuracy
(estimated vs. actual), and per-project time against target.

---

## 10. Web application

### 10.1 Stack

React 18 + TypeScript + Vite. TanStack Query for server state, Zustand for
local UI state. Tailwind for styling. `vite-plugin-pwa` (Workbox) for the
service worker and manifest. Deployed as static assets to S3 behind
CloudFront; content-hashed filenames with immutable caching, `index.html`
served `no-cache`.

Note this is the one place D7 costs something: the Python backend and the
TypeScript front end can't share types automatically. Mitigation — the API
is defined with Pydantic models, FastAPI emits an OpenAPI schema, and
`openapi-typescript` generates the client types in CI. The contract stays
single-sourced even though the languages don't.

### 10.2 Screens

| Screen | Purpose |
|--------|---------|
| **Today** (default) | Vertical timeline of the day: meetings, blocks, now-line. Tap a block to start/complete/defer. The screen you'll live in. |
| **Week** | 7-day grid, drag to reschedule (a drag pins the block), capacity bar per day. |
| **Tasks** | The full list: filter by project/priority/due, bulk edit, quick-add with NL parsing. |
| **Inbox** | Email candidates awaiting accept/dismiss. Empty most of the time by design. |
| **Brief** | Today's brief; archive of past briefs. |
| **Settings** | Working hours, deep-work windows, calendars to treat as blocking, notification channels, Google connection state, LLM model selection. |

### 10.3 Mobile

Not a separate app and not a shrunk desktop layout. Below 768px the UI
becomes: Today as a single scrolling column, a bottom tab bar
(Today / Tasks / Inbox / More), a floating quick-add button that opens
straight into NL text entry, and touch targets at 44px minimum. Week view
collapses to a 3-day horizontal scroll. `theme-color`, splash screens, and
`display: standalone` in the manifest so it launches chromeless from the
Home Screen.

### 10.4 Offline

Read-only offline in v1. The service worker caches the app shell plus the
current week's events and tasks. Offline, you can see your day; mutations
are disabled with a clear banner rather than optimistically queued.
Queued offline writes interact badly with a scheduler that rewrites events —
that's a v2 problem, and pretending otherwise would produce a data-loss bug
in week one.

---

## 11. API surface

All under `/api`, JSON, session-cookie authenticated.

```
GET    /me                          bootstrap: policy, calendars, connection state
POST   /auth/login  /auth/callback  /auth/logout
GET    /auth/google/status          connected | needs_reconnect | disconnected

GET    /tasks?status=&project=&due_before=
POST   /tasks                       {title,...} or {text: "..."} for NL parse
PATCH  /tasks/{id}
POST   /tasks/{id}/complete         idempotent; spawns next recurrence
POST   /tasks/{id}/defer            {until}
DELETE /tasks/{id}

GET    /events?from=&to=            merged Sundial + Google view
POST   /events                      Sundial-authored appointment
PATCH  /events/{id}                 409 if origin == "google"
DELETE /events/{id}

POST   /schedule/run                force a replan → returns the diff
GET    /schedule/proposal           pending large-diff proposal
POST   /schedule/proposal/accept
POST   /blocks/{id}/lock            pin a block

GET    /inbox                       email candidates
POST   /inbox/{id}/accept           {overrides?} → creates task
POST   /inbox/{id}/dismiss

GET    /brief/today
GET    /policy   PATCH /policy
POST   /push/subscribe  DELETE /push/subscribe

POST   /google/webhook              Google channel notifications (unauthenticated,
                                    validated by channel token + resource id)
```

Every mutating endpoint accepts an `Idempotency-Key` header. Errors follow
RFC 9457 problem+json.

---

## 12. Security and privacy

This application holds a Google refresh token that can read all of your mail.
That fact sets the security bar, single user or not.

- **Token at rest.** Refresh token encrypted with a customer-managed KMS key;
  only the `oauth` and sync Lambda roles hold `kms:Decrypt` on it. Access
  tokens live in Lambda memory only.
- **IAM.** One role per Lambda, each scoped to the specific table, key, queue,
  and secret it uses. No wildcard resources. The `api` role cannot decrypt the
  Google secret at all.
- **Transport.** HTTPS only; HSTS; TLS 1.2 minimum at CloudFront.
- **Browser.** CSP with no `unsafe-inline`, `frame-ancestors 'none'`,
  strict `Referrer-Policy`, `SameSite=Lax` session cookie, CSRF token on
  state-changing requests.
- **Webhook.** The Google webhook endpoint is unauthenticated by necessity;
  it validates the channel token and resource id, does no work beyond
  enqueueing, and is rate-limited at API Gateway.
- **Data minimisation.** Email bodies are **never persisted**. An
  `EmailCandidate` keeps the subject, sender, a short snippet, and the Gmail
  message id — enough to show you why it was proposed and to link out to the
  real message. Full bodies exist only in memory during a triage call.
- **LLM data flow.** Subject, sender, and up to ~1500 characters of body text
  go to the Anthropic API for messages that survive pre-filtering. This is
  stated in Settings with a per-sender-domain exclusion list, and a global
  "never send email content to an LLM" switch that reduces J3 to
  manual-promotion mode.
- **Secrets.** No credentials in the repo. CDK reads config from SSM
  Parameter Store; CI uses an OIDC-federated deploy role, not long-lived keys.
- **Logging.** No email bodies, no tokens, no full event titles at INFO. 7-day
  retention. Structured JSON logs with a correlation id per request.
- **Backup.** DynamoDB PITR (35 days) plus a weekly on-demand backup. A
  documented, tested restore procedure — an untested backup is a rumour.

---

## 13. Operations

- **IaC.** AWS CDK in Python. Two stacks: `SundialInfra` (durable — table,
  key, secrets, domain, certificate) and `SundialApp` (functions, API, CDN).
  Destroying the app stack must never take data with it.
- **Environments.** `dev` and `prod`, separate AWS accounts if convenient,
  separate Google OAuth clients regardless.
- **CI/CD.** GitHub Actions: lint (`ruff`, `mypy`, `eslint`, `tsc`) → test →
  `cdk diff` on PR → deploy on merge to `main` via OIDC role.
- **Observability.** CloudWatch dashboard: sync lag, scheduler runtime, queue
  depth, DLQ depth, Google 4xx/5xx rate, LLM spend per day. Alarms on DLQ
  depth > 0, Google auth failure, scheduler p99 > 5s, and daily LLM spend
  above a configured ceiling — email to you via SNS.
- **Kill switches.** Feature flags in the policy document for: auto-schedule,
  Gmail triage, push, LLM entirely. Any one can be turned off without a
  deploy.
- **Runbooks.** Reconnect Google · rebuild from full resync · disaster restore ·
  rotate the KMS key · revoke and re-issue the session signing key.

---

## 14. Testing

- **Unit** — scheduler placement, scoring, free/busy arithmetic, timezone and
  DST edges (the spring-forward day is a fixture, not an afterthought).
- **Property-based** (Hypothesis) on the scheduler: for any policy and task
  set, no two blocks overlap, nothing lands outside working windows, no block
  is placed after its due date without being flagged, and rerunning on
  unchanged input produces an empty diff (idempotence).
- **Contract** — recorded Google API fixtures covering: sync token expiry
  (410), rate limit (403 `rateLimitExceeded`), a moved event, a deleted
  event, and a duplicated invite.
- **Integration** — DynamoDB Local + moto, full flows: create task →
  schedule → mirror to Google → complete → block removed.
- **LLM** — a golden set of ~50 real emails (redacted) with expected
  actionable/not labels; a regression run reports precision and recall.
  Triage prompt changes are evaluated against it before merge, not vibes.
- **E2E** — Playwright over the deployed dev stack for the critical paths,
  run on desktop and mobile viewports.

---

## 15. Delivery plan

Each phase ends with something genuinely usable, and J1 is real before any
automation lands on top of it.

| Phase | Scope | Done when |
|-------|-------|-----------|
| **M0 — Foundations** *(~1 wk)* | CDK stacks, table, CI/CD, Google OAuth round-trip, session auth, deployed empty PWA shell | You can sign in on your phone and see "connected". |
| **M1 — Read-only calendar** *(~1 wk)* | Calendar pull sync, sync tokens, webhook + renewal, Today and Week views | Sundial shows your real calendar on your phone, correctly, all week. |
| **M2 — Tasks (J1)** *(~1.5 wk)* | Task CRUD, projects, due/defer, Tasks view, quick-add, Google Calendar alert reminders | Sundial replaces your todo app. **This is the first phase that stands alone.** |
| **M3 — Time blocking (J2)** *(~2 wk)* | Policy editor, scheduler, block write path to the Sundial calendar, drag-to-pin, stability rules, overcommitment surfacing | Your todos appear on your phone's calendar at sensible times and survive a week of real use without irritating you. |
| **M4 — Brief + push (J4)** *(~1 wk)* | Web push, SES daily brief, weekly review, slipped-task handling | You start the day in Sundial instead of in your inbox. |
| **M5 — Email triage (J3)** *(~1.5 wk)* | Gmail sync, pre-filter, Claude triage, Inbox tray, dismissal memory, exclusion settings | Sundial proposes tasks you'd actually have written yourself. |
| **M6 — Polish** *(ongoing)* | Estimate-accuracy feedback loop, offline read, keyboard shortcuts, search, per-project time targets | |

M5 sits last on purpose: it is the phase gated behind the OAuth decision in
§5.3, and it is the only phase that fails gracefully if you never ship it.

---

## 16. Open questions

1. **§5.3 — Workspace account, Calendar-only first, or weekly re-auth?**
   Blocks the Gmail phase and nothing else.
2. Domain name for the deployment (`sundial.<yourdomain>`)?
3. Should completed blocks record **actual** time spent (start/stop timer)?
   It's the input that makes estimates get better over time, at the cost of
   asking you to press a button.
4. Which existing calendars count as blocking — all of them, or a chosen set?
   (Default: all, editable in Settings.)
5. Do you want a "shut down" ritual — an evening prompt to clear tomorrow's
   plan — or is the morning brief enough?
6. Hard stop on working hours, or is Sundial allowed to schedule into the
   evening when a deadline demands it?

---

## Appendix A — Glossary

| Term | Meaning |
|------|---------|
| **Task** | Something to be done. Has effort, may have a deadline. |
| **Block** | A calendar event Sundial created to do a task in. |
| **Appointment** | An event with an inherent time (a meeting), not derived from a task. |
| **Foreign event** | Any event Sundial did not create. Read-only, blocking. |
| **Candidate** | A proposed task from Gmail, awaiting your verdict. |
| **Policy** | The rules describing when you're willing to work and on what. |
| **Horizon** | How far ahead the scheduler plans. Default 14 days. |
