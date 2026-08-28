# Sundial — Technical Specification

**Status:** Draft v0.2
**Owner:** Joe McMahon
**Last updated:** 2026-08-21

> **v0.2 changes.** §5.3 rewritten — the 7-day token expiry is bound to
> *publishing status*, not scope sensitivity, so it gates every phase, not
> just Gmail; a fourth option (publish unverified) is added and recommended.
> `SchedulingPolicy` (§3.1), task recurrence (§3.1), reminder origination
> (§3.1), and scheduler weights (§7.2) now have real schemas. §6.4 gains the
> echo-suppression rule without which every block locks itself. Corrections
> to §3.2, §4.2, §6.3, §8.2, §8.4, §10.1, §11, §12, §16.

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

The diagram above states the *authority* rule. It is deliberately refined for
one case: a Sundial-origin **block** that the user drags in Google Calendar is
adopted, not reverted (§6.5). That is not a violation of D1 — a drag is the
user editing their own plan through a different client, and the alternative is
a system that fights its owner. Read §6.5 as authoritative wherever it is more
specific than this diagram.

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
| `recurrence` | RRULE string\|null | RFC 5545 subset; evaluated only on completion |
| `recurrence_parent_id` | ULID\|null | the instance this one was spawned from |
| `recurrence_anchor` | enum | `due_date` / `completion_date` — what the next occurrence is computed from |
| `started_at` | timestamp\|null | set when work starts, cleared on complete |
| `actual_minutes` | int\|null | summed across work sessions; null if never timed |

**Recurrence.** Sundial does not own an RRULE *expansion* engine (§6.6). A
recurring task exists as exactly one live instance at a time; completing it
computes the next occurrence from `recurrence` + `recurrence_anchor` and
creates a fresh Task with a new `task_id`, `status = active`, and
`recurrence_parent_id` pointing back. Only `FREQ=DAILY|WEEKLY|MONTHLY` with
`INTERVAL`, `BYDAY`, and `COUNT`/`UNTIL` are accepted in v1; anything else is
rejected at the API boundary rather than silently misinterpreted.

**Actual time.** `actual_minutes` and `started_at` are what make §8.3's
estimate feedback loop possible. They are carried from M2 even though the
start/stop UI is M6 work — nullable columns are cheap, a schema migration over
live data is not. This resolves the schema half of §16 Q3; whether the UI ever
asks you to press the button stays open.

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

**Reminder** — `(reminder_id, target_id, target_type, kind, fire_at,
channels[], state)`. `target_type` is `task` or `event`; `state` is
`scheduled` / `fired` / `cancelled`.

Reminders are **derived, never hand-managed**: a materializer recomputes them
from the policy's `reminder_defaults` whenever a target changes, and they are
the only thing §9.1's Stream handler acts on.

| `kind` | Target | Default lead | Channel |
|--------|--------|--------------|---------|
| `block_start` | Event, `kind = block` | 5 min before `start` | Google Calendar alert — **no Sundial row is written** |
| `due_soon` | Task with `due_at` | 24 h and 2 h before | web push |
| `defer_expiry` | Task with `defer_until` | at `defer_until` | web push |
| `overcommit` | — | at `brief_time` | web push, only when §7.5 trips |
| `brief` | — | daily at `brief_time` | SES |

`block_start` is deliberately not materialised: it rides Google's native
alert, costs nothing, and fires even when Sundial is down (§9.1). This is also
why the scheduler churning blocks does not churn reminders. Every other kind
writes a `REM#<fire_at>#<id>` row. Editing, completing, deferring, or deleting
a target cancels its reminders and their one-shot schedules.

**EmailCandidate** — a proposed task awaiting your verdict:
`(gmail_message_id, thread_id, from, subject, snippet, proposed_task{...}, confidence, state: pending/accepted/dismissed)`.
Dismissals are remembered so the same message is never re-proposed.

**SchedulingPolicy** — one singleton document (`POLICY#v1`). This is the
scheduler's entire configuration surface and the Settings screen's data model,
so it is specified in full: §7 is unimplementable without it.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `version` | int | 1 | bumped on every PATCH; optimistic concurrency |
| `timezone` | IANA string | from browser | the user's *current* zone (§6.7) |
| `horizon_days` | int | 14 | how far ahead the scheduler plans |
| `working_windows` | `{weekday: [{start,end}]}` | Mon–Fri 09:00–17:00 | local `HH:MM`; multiple windows per day, so lunch is simply a gap between two |
| `deep_windows` | `{weekday: [{start,end}]}` | Mon–Fri 09:00–12:00 | must be a subset of `working_windows` |
| `admin_windows` | `{weekday: [{start,end}]}` | ∅ | empty ⇒ `admin` may go anywhere in `working_windows` |
| `energy_policy` | `{deep:[…], shallow:[…], admin:[…]}` | see below | which window set each energy class may occupy |
| `min_chunk_minutes` | int | 25 | global floor; a Task may raise it, never lower it |
| `max_block_minutes` | int | 120 | longest single block; longer work splits if `splittable` |
| `max_blocks_per_day` | int | 6 | |
| `max_scheduled_minutes_per_day` | int | 300 | capacity ceiling; drives §7.5 |
| `min_gap_minutes` | int | 10 | between two adjacent Sundial blocks |
| `meeting_buffer_before_minutes` | int | 5 | |
| `meeting_buffer_after_minutes` | int | 10 | |
| `no_schedule_before` / `no_schedule_after` | `HH:MM`\|null | null | hard clamps on top of `working_windows` |
| `blocking_calendar_ids` | string[] | all discovered | which calendars count as busy (§16 Q4) |
| `protected_ranges` | `[{weekday,start,end,label}]` | ∅ | never scheduled even inside a working window — this is where "protect Fridays after 15:00" lives |
| `allow_evening_overflow` | bool | false | when true, deadline-risk tasks may use `overflow_windows` (§16 Q6) |
| `overflow_windows` | `{weekday: [{start,end}]}` | ∅ | only consulted when `allow_evening_overflow` |
| `weights` | object | §7.2 | scheduler scoring weights |
| `reminder_defaults` | object | §3.1 Reminder | lead times per reminder kind |
| `brief_time` | `HH:MM` | 06:30 | local |
| `weekly_review_time` | `{weekday, HH:MM}` | Sun 18:00 | |
| `notification_channels` | `{push,email,calendar}` | all true | |
| `llm` | `{triage_model, parse_model, brief_model, confidence_threshold, excluded_sender_domains[], enabled}` | §8.4 | |
| `flags` | `{auto_schedule, gmail_triage, push, llm}` | all true | §13 kill switches |

Default `energy_policy`: `deep → deep_windows`, `shallow → working_windows`,
`admin → admin_windows if non-empty else working_windows`.

Every wall-clock time here is local to `timezone` and is resolved against the
user's *current* zone at scheduling time (§6.7) — never stored as UTC.
`allow_evening_overflow` is how §16 Q6 is answered without a code change; the
default of `false` is the hard stop.

### 3.2 Storage: DynamoDB single table

One table, `sundial`, on-demand billing. Single user means the partition key
is coarse by design — item counts are in the thousands, not millions.

| Entity | PK | SK | Notes |
|--------|----|----|-------|
| Task | `USER#<uid>` | `TASK#<task_id>` | |
| Event | `USER#<uid>` | `EVENT#<iso_start>#<event_id>` | SK sorts by time; range queries for a day/week are a single `Query` |
| Calendar | `USER#<uid>` | `CAL#<google_calendar_id>` | discovered from `calendarList`; carries `is_sundial` and `primary` |
| Project | `USER#<uid>` | `PROJ#<project_id>` | |
| EmailCandidate | `USER#<uid>` | `EMAIL#<gmail_message_id>` | natural dedupe key |
| Policy | `USER#<uid>` | `POLICY#v1` | |
| SyncState | `USER#<uid>` | `SYNC#<resource>` | Google sync tokens, Gmail historyId, watch channel expiry |
| Reminder | `USER#<uid>` | `REM#<fire_at>#<reminder_id>` | |
| OAuth tokens | `USER#<uid>` | `AUTH#google` | refresh token encrypted with KMS (§12) |
| Idempotency | `USER#<uid>` | `IDEM#<key>` | request hash + stored response; `ttl` attribute, 24 h (§11) |
| PushSub | `USER#<uid>` | `PUSH#<endpoint_hash>` | one row per browser (§9.1) |

**Calendars are stored, not derived.** `(calendar_id, summary, primary,
is_sundial, access_role, time_zone)`, refreshed from `calendarList` on every
sync run. Three things need this row and none of them can reconstruct it:
§6.1's "record the id of the `Sundial` calendar at first run", §3.1's
`blocking_calendar_ids`, which is a policy selecting from a list that must
therefore exist, and §6.5's invite-dedupe rule, which needs to know *which*
calendar is primary in order to prefer its copy. `is_sundial` is matched on
summary at creation time, because the id is precisely what is not yet known
the first time discovery runs.

**GSI1 — lookup by Google id.** `GSI1PK = GEVT#<google_event_id>`,
`GSI1SK = <event_id>`. Needed on every inbound sync page to answer "do I
already know this event?"

**GSI2 — task work queues.** `GSI2PK = STATUS#<status>`,
`GSI2SK = <due_at or 9999>#<priority>`. Drives the scheduler's candidate list
and the "what's due" views without a table scan.

**Event items are keyed by a mutable field.** `EVENT#<iso_start>#<event_id>`
sorts by time, which is what makes a day or week query a single `Query` — but
it also means **moving a block is a delete + put, not an update**. The
scheduler's commit step (§7.5) must express a reschedule as a
`TransactWriteItems` pair, never an `UpdateExpression` on `start`. Anything
that holds an `EVENT#` sort key across a reschedule is holding a stale pointer.

**Soft-deleted events stay inside the range.** `deleted_at` does not remove the
item from the SK range, so every read path — `/events`, the scheduler's
free/busy build, the brief, the capacity bar — must filter `deleted_at = null`.
This is the easiest bug to introduce in this schema and the hardest to notice,
because it manifests as phantom busy time rather than an error.

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
| Secrets Manager (4 secrets — §12) | $1.60 |
| KMS CMK | $1.00 |
| SES | ~$0.00 (under 1k mails) |
| EventBridge Scheduler | $0 (free tier) |
| CloudWatch logs (7-day retention) | ~$0.50 |
| Claude API (Claude Opus 5) | ~$3–9 (see §8.4) |
| **Total** | **~$8–14/month** |

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

**The rule, stated precisely.** A Google Cloud project whose OAuth consent
screen is user type **External** and publishing status **Testing** is issued
refresh tokens that **expire after 7 days**. The only carve-out is an app
requesting nothing beyond `openid`, `email`, and `profile`.

Two consequences follow, and the first was wrong in v0.1 of this spec:

1. **The 7-day expiry is bound to publishing status, not scope sensitivity.**
   It is *not* a Gmail problem. A Calendar-only Sundial left in Testing status
   dies every seventh day exactly as thoroughly as one with Gmail scopes. This
   gates **M0 through M5**, not just M5.
2. **Publishing status and verification status are different things.** Moving
   to Production removes the 7-day expiry immediately. Verification is what
   removes the *warning screen*. You can have the first without the second.

**Four options:**

| # | Option | Token life | Cost | Catch |
|---|--------|-----------|------|-------|
| A | **Publish to Production, unverified** | long-lived | none | "Unverified app" interstitial before each consent; hard lifetime cap of 100 users on the project |
| B | **Workspace account, app marked Internal** | long-lived | a Workspace seat | requires a Workspace domain you control |
| C | **Publish to Production, verified** | long-lived | CASA assessment, multi-week | aimed at products, not personal tools |
| D | **Stay in Testing** | **7 days** | none | re-consent weekly, forever, across every phase |

**Recommendation: A, falling back to B.**

Option A is the right default and Google documents it as an intended path —
their guidance explicitly names "you're the only user of your app, or it's
used by only a few users known personally to you" as a case where advancing
through the unverified screen is reasonable. You click through one scary
interstitial at first consent and then have long-lived tokens with the full
scope set, including the restricted Gmail scopes. The 100-user cap is
irrelevant to a single-user app; it is permanent and cannot be reset, which is
also irrelevant here.

Option B remains strictly safer if you already have a Workspace domain:
Internal apps sidestep the unverified screen and the user cap entirely, and
they are not exposed to Google tightening restricted-scope enforcement.

The residual risk on A is real but bounded: Google has tightened Gmail
restricted-scope handling before and could do so again, which would strand J3
without touching J1/J2/J4. Since M5 is last in the delivery plan anyway, that
risk is already sequenced correctly — but it is a reason to prefer B if the
choice is free.

**Option D is not viable** and should not be treated as the "cheap" fallback
it looked like in v0.1. It breaks background sync in every phase.

**Two operational consequences, both easy to miss:**

- Publishing status is a **Cloud Console UI-only setting with no API**. It
  cannot be expressed in CDK. It belongs in the M0 runbook as a manual,
  checklist-item step, performed *before* the first real OAuth round-trip.
- §13 calls for separate Google OAuth clients per environment. **The `dev`
  client must be published too**, or the dev environment breaks weekly while
  prod is fine — which is a genuinely confusing failure to debug.

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
              if is_own_echo(google, existing):        # see §6.4.1 — CHECK THIS FIRST
                  existing.google_etag = google.etag   # absorb, do nothing else
              elif google.etag != existing.google_etag:
                  DRIFT → see §6.5
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
`sync_cal` worker then does an incremental sync. Channels expire; the
`events.watch` response carries an `expiration` timestamp, which Sundial
stores on the `SYNC#<calendar_id>` item and renews from. **Do not hard-code a
TTL** — Google sets it, has changed it, and may cap it below what you ask for.
A daily EventBridge rule renews any channel expiring within 48 hours.

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
`sundial_event_id`, so a retried create is a no-op rather than a duplicate.

**Encoding, precisely.** Google's event id charset is base32hex — digits `0-9`
and lowercase `a-v`, length 5–1024. A ULID's canonical text form is *Crockford*
base32, which uses `a-z` minus `i`,`l`,`o`,`u` and therefore can contain
`w`,`x`,`y`,`z`. **Lowercasing a ULID is not sufficient** and will produce a
`400 invalid` on roughly a third of ids, intermittently, in a way that looks
like a transient Google fault. Decode the ULID to its raw 128 bits and
re-encode those bits as base32hex.

**Deleted ids are not reusable.** Google reserves the id of a deleted event; a
create that reuses one fails with `409 duplicate`. This matters at exactly one
seam: §6.5's "Sundial block deleted in Google" case. The rule is that a deleted
block's `event_id` is **retired** — reclaiming that time produces a *new* ULID
and therefore a new Google id. A `409` on create is treated as a signal that a
retired id leaked through, and is resolved by minting a fresh `event_id`, not
by retrying.

#### 6.4.1 Echo suppression — read this before implementing §6.2

Sundial writes a block to Google. Google fires the watch notification for that
very write. `sync_cal` pulls, finds an `origin = "sundial"` event whose etag no
longer matches the stored one, and concludes the user dragged it — so §6.5
marks it `locked = true`. **Every block Sundial creates locks itself within
seconds, and by day two the scheduler has nothing left it is allowed to move.**

This is the defining failure mode of a bidirectional sync with a webhook, and
it will not show up in unit tests because it needs a real round-trip. Three
rules prevent it:

1. **Persist the etag from the write response, before the webhook can land.**
   `events.insert` and `events.update` return the created resource including
   its `etag`. Store it on the Event item in the *same* commit that records the
   successful write. An etag written asynchronously afterwards is a race, and
   the race is one you lose often.
2. **Carry a revision counter.** `extendedProperties.private.sundial_rev` is
   incremented on every Sundial-originated write and stored locally. On pull,
   `google.sundial_rev == existing.sundial_rev` means the payload is Sundial's
   own — regardless of etag. This is the authoritative check and the reason the
   field exists.
3. **`is_own_echo(google, existing)` is true when either** the etags match, or
   the revs match and the time fields are unchanged. Only when both tests fail
   is it a genuine user edit and DRIFT per §6.5.

Rule 2 exists because rule 1 alone is not enough: Google may re-issue an etag
for a resource Sundial did not touch. Rule 1 exists because rule 2 alone is not
enough: a write that succeeds at Google but whose local commit fails leaves the
rev behind. Implement both.

**Symptom to watch for in M3:** blocks acquiring `locked = true` without anyone
touching Google. Add a CloudWatch metric for auto-locks per day; a healthy
system has approximately zero.

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

These are **tuning constants living in `policy.weights`**, not code. Starting
values, chosen so that a P1 due tomorrow beats a P3 due next month and nothing
starves past a fortnight:

| Constant | Default | Meaning |
|----------|---------|---------|
| `w_urgency` | 1.0 | reference weight; the others are relative to it |
| `w_priority` | 0.6 | |
| `w_age` | 0.15 | per day since creation |
| `w_project_debt` | 0.4 | |
| `priority_weight` | `{P1: 1.0, P2: 0.65, P3: 0.35, P4: 0.15}` | |
| `age_cap_days` | 14 | anti-starvation bonus stops growing here |
| `urgency_ramp` | see below | |

`urgency(due_at)` given `h` hours until due: `1.0` if `h ≤ 0` (overdue),
`0.9 + 0.1·(1 − h/48)` for `0 < h ≤ 48`, `0.4 + 0.5·(168 − h)/120` for
`48 < h ≤ 168`, `0.4·(1 − (h − 168)/672)` beyond a week, floored at `0`. Tasks
with no `due_at` score `urgency = 0` and rely on priority and age.

The shape matters more than the numbers: steep inside 48 h, near-flat beyond a
week. Tune the numbers against real use; do not tune them in a unit test.

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

Two things this snippet does not show and the implementation must:

- **Handle `stop_reason == "refusal"`.** Email bodies are untrusted text and
  are exactly the kind of input that trips a safety classifier. A refusal
  returns **HTTP 200** with `stop_reason: "refusal"` and no usable content, so
  code that reads `response.content` without checking `stop_reason` sees a
  batch that silently triaged nothing. Check `stop_reason` first; on refusal,
  drop the batch, log the `stop_details.category`, and fall back to bisecting
  the batch so one hostile message does not cost you the other nine. Enabling
  the server-side `fallbacks` parameter is the low-effort version of this.
- **Give the cached prefix room to be cached.** Prompt caching has a **~1024
  token minimum prefix**; a `TRIAGE_SYSTEM` shorter than that silently does not
  cache, with no error and no warning. Verify with
  `usage.cache_read_input_tokens > 0` on the second call — if it is `0` across
  repeated runs, the §8.4 cost estimate does not hold.

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

One thing that estimate does not include: **adaptive thinking is on by
default on Opus 5, and thinking tokens bill as output.** At `effort: "low"` the
overhead is modest, but it is not zero and it is the most likely reason a real
bill lands above this range. Meter it before assuming the range holds — §13
already alarms on daily LLM spend, and that alarm is the backstop.

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

React 19 + TypeScript + Vite. TanStack Query for server state, Zustand for
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

GET    /projects                    POST /projects
PATCH  /projects/{id}               DELETE /projects/{id}

GET    /inbox                       email candidates
POST   /inbox/{id}/accept           {overrides?} → creates task
POST   /inbox/{id}/dismiss

GET    /brief/today
GET    /brief?before=               archive of past briefs
GET    /review/weekly               latest weekly review
GET    /policy   PATCH /policy
POST   /push/subscribe  DELETE /push/subscribe

POST   /google/webhook              Google channel notifications (unauthenticated,
                                    validated by channel token + resource id)
```

Every mutating endpoint accepts an `Idempotency-Key` header, stored as an
`IDEM#<key>` item (§3.2) holding a hash of the request body and the response
that was returned, with a 24-hour TTL. A replay with a matching hash returns
the stored response; a replay with the *same* key and a *different* body is a
`409`, not a silent overwrite.

Errors follow RFC 9457 problem+json.

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
  strict `Referrer-Policy`, `SameSite=Lax` session cookie. CSRF uses
  double-submit: at login the `oauth` Lambda sets a second, **non-`HttpOnly`**
  `sundial_csrf` cookie holding a random 32-byte token, and the SPA reads it
  and echoes it in an `X-CSRF-Token` header on every mutating request.
  `SameSite=Lax` already blocks the cross-site form-post case; this covers the
  rest.

  **The comparison happens in the application, not in the Lambda authorizer.**
  The authorizer validates the session JWT and nothing else. This split is
  forced by the authorizer's cache and is not a matter of taste: an HTTP API
  authorizer caches its verdict against `identitySource` for 300s (§5.1), so
  either the CSRF header is part of that key — and every `GET`, which carries
  no such header, is rejected by API Gateway for a missing identity source
  before the authorizer is even invoked — or it is not, and a single failed
  `POST` caches a denial that locks out reads for the next five minutes. Both
  failure modes are intermittent and would be miserable to diagnose. Being
  per-request is the whole point of the check, and the authorizer is the one
  place in the stack that is deliberately not per-request.

  `identitySource` is therefore `$request.header.Cookie` alone. The
  application rejects any state-changing call where header and cookie are
  absent or disagree, with the exception of `/api/auth/login` and
  `/api/auth/callback` — which is where the token comes from — and the Google
  webhook, which has no session at all and is validated by channel token.
- **Secrets inventory** (four, per §4.2): the Google OAuth client secret, the
  Anthropic API key, the RS256 session-signing private key (§5.1), and the
  VAPID private key (§9.1). The Google *refresh* token is not among them — it
  lives KMS-encrypted in the `AUTH#google` item (§5.4).
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

**M0 has two prerequisites that are not code** and that no later phase can
route around: the OAuth publishing decision (§5.3) and the domain name (§16
Q2). The first is not, as v0.1 claimed, an M5 concern — an unpublished app
breaks background sync in M1 and never recovers.

M5 still sits last, but for a different reason: it is the phase whose *scope*
carries residual external risk (§5.3), and it is the only phase that fails
gracefully if you never ship it.

### 15.1 M0 split: what the domain actually gates

Per §16 decision 2 the domain is deferred, which splits M0 in two. The split
is along a real seam — the redirect URI and the cookie domain — not an
arbitrary one.

**M0a — localhost, no domain needed.** Repo skeleton and toolchain; the
`SundialInfra` stack (table, GSIs, KMS key, secrets, SSM parameters); the
`SundialApp` stack synthesising to `cdk.out` and passing `cdk synth` in CI;
the FastAPI app with its internal router; session JWT mint and verify; the
Lambda authorizer including the §12 CSRF double-submit check; the full Google
authorization-code flow with PKCE against `http://localhost:5173`; the KMS
envelope-encrypted refresh token in `AUTH#google`; `GET /api/me` and
`GET /api/auth/google/status`; the Vite PWA shell showing connection state;
and CI running lint, test, and `cdk diff`.

**M0b — needs the domain.** Route 53 hosted zone; the ACM certificate in
`us-east-1`; the CloudFront distribution with S3 and the API Gateway as a
second origin under `/api/*`; the production redirect URI; and the first real
deploy. Only then is M0's "sign in on your phone and see connected" true.

The cookie is the reason M0b cannot be faked locally: §5.1's first-party
session cookie depends on CloudFront serving the SPA and the API from one
origin. Locally that is a Vite dev-server proxy, which is a *different*
mechanism reaching the same place. It is close enough to build against and
not close enough to call M0 done.

---

## 16. Open questions

Six in v0.1; two of those are now closed in the schema and one was misfiled.

**Decided (2026-08-28):**

1. **§5.3 — publishing path: option A**, publish to Production unverified.
   The consequence for M0 is a manual Cloud Console step, for *both* the `dev`
   and `prod` clients, performed before the first real OAuth round-trip.

   One refinement on top of A: the OAuth client requests **only the calendar
   scopes** (`openid`, `email`, `profile`, `calendar.events`,
   `calendarlist.readonly`) until M5. The restricted Gmail scopes carry the
   residual verification risk described above; adding them at M5 keeps that
   risk off M0–M4 entirely and costs one extra consent screen when J3 lands.

2. **Domain name: deferred, localhost-first.** M0 develops and tests against
   `http://localhost:5173` with a matching redirect URI on the `dev` client.
   The domain is chosen before the first deploy, because the hosted zone, ACM
   certificate, CloudFront alias, and production redirect URI all derive from
   it. What this splits M0 into is set out in §15.1.

3. **AWS accounts: one account, two environments.** `dev` and `prod` are
   distinguished by resource-name suffix and SSM parameter path
   (`/sundial/<env>/...`), not by account boundary. Separate Google OAuth
   clients per environment still applies (§13). The single CI deploy role is
   OIDC-federated and scoped by that path prefix.

**Closed in the schema, still open as product decisions:**

- **Q3 — actual time spent.** The Task entity now carries `started_at` and
  `actual_minutes` (§3.1) so nothing has to be migrated later. Whether the UI
  ever asks you to press start/stop is an M6 call, and the estimate feedback
  loop in §8.3 degrades to "no data" rather than breaking if you never do.
- **Q4 — which calendars block?** Default is all, editable via
  `policy.blocking_calendar_ids` (§3.1). No further decision needed to start.
- **Q6 — hard stop on working hours?** Default is yes:
  `allow_evening_overflow` defaults to `false` (§3.1). Flipping it is a
  settings change, not a code change.

**Genuinely open, blocks nothing:**

- **Q5 — a "shut down" ritual?** An evening prompt to clear tomorrow's plan,
  or is the morning brief enough? Pure addition to M4; decide after living
  with the brief for a week.

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
