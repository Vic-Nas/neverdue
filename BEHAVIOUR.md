# NeverDue — Processing Behaviour Reference

This document is the single source of truth for how jobs, events, and reprocessing
must behave. Any code change that would violate a rule here is wrong — fix the code,
not this document.

---

## Core invariant

**One source input → one ScanJob, forever.**

An email, an upload, or a reprocess action each produces exactly one ScanJob.
A reprocess does NOT create a new job — it mutates the existing one.
Jobs are never duplicated, abandoned, or silently replaced.

---

## ScanJob lifecycle

```md
queued → processing → done
                    → needs_review   (one or more events are pending; user must act)
                    → failed         (unhandled exception, scan limit, or plan restriction)

needs_review → processing            (user submits a reprocess prompt)
             → done                  (user explicitly cancels with no prompt)

failed → queued                      (manual retry by user or admin, or auto-retry on
                                      quota reset / plan upgrade)
```

### Status definitions

| Status | Meaning |
| --- | --- |
| `queued` | Job created, not yet picked up by a worker |
| `processing` | Worker is actively running |
| `done` | All events created are active; nothing left to do |
| `needs_review` | One or more events are `pending`; user must review and resubmit |
| `failed` | Job could not be completed — see `failure_reason` for why |

### What must never happen

- A job with pending events is marked `done`. That is a lie.
- A job is marked `done` with zero events and no note explaining why.
- A reprocess creates a new ScanJob. There is one job per source input.
- Two jobs race to write events for the same source.
- A failed job is silently deleted before the user has seen it.

---

## Failure reasons

Every failed job must have a `failure_reason` code. This enables the admin to
filter and bulk-retry by root cause, and shows the user a meaningful message.

| Code | Meaning | Retried automatically |
| --- | --- | --- |
| `llm_error` | Anthropic API failure — rate limit, outage, credits exhausted | No — manual retry by user or admin |
| `scan_limit` | Monthly scan quota reached | Yes — on month reset (`reset_monthly_scans`) and on plan upgrade |
| `pro_required` | Attachment-only email received on free plan | Yes — on plan upgrade (`retry_jobs_after_plan_upgrade`) |
| `internal_error` | Unhandled exception — bug or infra failure | No — manual retry after fix; grouped by `failure_signature` in admin |

`failure_signature` stores a short exception identifier (class + first line of message,
e.g. `"AnthropicError: 529 overloaded"`) so `internal_error` jobs can be grouped by
root cause in the staff dashboard and bulk-retried when a fix is deployed.

### What must never happen

- A job is marked `failed` without a `failure_reason`.
- A `scan_limit` or `pro_required` job is marked `done` — they are failures, not successes.
- A `failed` job is auto-deleted by `cleanup_events`. Only `done` jobs are cleaned up.

---

## Free user — attachment behaviour

Free users may forward emails that contain attachments.

- If the email has a **usable body** (non-empty text): process the body only.
  Set a note: `"Attachments ignored — upgrade to Pro to include them."` Job → `done`.
- If the email is **attachment-only** (no usable body): the job cannot be processed
  without the attachment. Mark the job `failed` with `failure_reason=pro_required`.
  The job stays visible in the queue with a message to upgrade.
  When the user upgrades, `retry_jobs_after_plan_upgrade` re-enqueues the job automatically.

**What must never happen:**

- An attachment is silently stripped with no user-visible consequence.
- An attachment-only email for a free user is marked `done` with zero events and no explanation.

---

## Event status

| Status | Meaning |
| --- | --- |
| `active` | Confirmed, written to calendar if GCal is connected |
| `pending` | Needs user review — incomplete, ambiguous, or conflicting data |

### Pending event rules

- Every pending event must have a non-empty `concern` explaining why it is pending.
- Every pending event should have a `pending_expires_at` date if determinable.
- Pending events are NEVER written to GCal.
- Pending events linked to a job keep that link so the job detail page can show them.

### All-or-nothing batch rule

If any event in a batch is `pending`, the entire batch flips to `pending`.
This keeps each job homogeneous: the user reviews the whole batch together,
not a partial subset. Active events in the same batch get concern:
"Other events in this batch needed attention."

This rule applies at save time in `llm/pipeline/saving.py:_save_events`, not at extraction time.

---

## LLM extraction behaviour

The LLM must preserve all data even when information is missing.
A missing recurrence end date is NOT a reason to drop the event —
it is a reason to mark it `pending` with a clear concern.
The user will supply the missing information in the reprocess prompt.

**The LLM must never silently discard events.**
If something is unclear, mark it pending and explain why.

---

## Reprocess flow

1. User opens a `needs_review` job in the queue.
2. User reads the pending events and their concerns.
3. User writes a correction prompt (e.g. "repeat yearly until 2030-01-01").
4. Frontend calls `reprocess_events.defer(user_id, event_ids, prompt, job_pk=job.pk)` (via Procrastinate).
5. Worker sets job status → `processing` (`emails/tasks/reprocess.py`).
6. Worker reads and serializes event data (including `source_email_id`) from the
   pending events **before** doing anything else.
7. Worker calls `process_text(user, prompt, source_email_id=preserved_id, scan_job=job)` (`llm/pipeline/entry.py`).
8. `_save_events` (`llm/pipeline/saving.py`) writes new events linked to the **same** job.
9. **Only after a successful LLM response**, worker deletes the pending events.
10. If all new events are active → job → `done`.
11. If any new events are pending → job → `needs_review` (user acts again).

### What must never happen during reprocess

- `source_email_id` is lost. Future identical emails would bypass dedup.
- A new ScanJob is created. The original job is the permanent record.
- The original job ends up with zero events and no explanation.
- Pending events are deleted before the LLM call succeeds — if the LLM fails,
  events must remain intact so the job is still recoverable.
- Job status is set to `done` by the task — the pipeline owns that decision.

---

## Conflict detection

When `_save_events` (`llm/pipeline/saving.py`) processes a new event, it checks for conflicts against
existing active events for the same user:

**Conflict conditions (either triggers):**

1. An active event exists with the same `source_email_id` — same email was already
   processed and produced active events.
2. An active event exists with a matching title AND overlapping time window
   (within ±1 hour of the start time).

**When a conflict is found:**

- The new event remains `pending`.
- The conflict details are **appended** to its `concern` field:
  `" Conflicts with existing event: '{title}' on {date} (id={pk})."`
- The user sees exactly what clashes, and can tell the reprocess prompt
  to cancel, replace, or merge.

**What must never happen:**

- A conflict silently overwrites an existing active event.
- A conflict is detected but not surfaced to the user.
- The presence of a conflict causes data to be dropped.

---

## Duplicate email guard

`process_inbound_email` (`emails/tasks/processing.py`) checks before processing:

```python
if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
    # mark job done with note, return early
```

This guard depends on `source_email_id` being set on events.
Events created by reprocess MUST carry the original `source_email_id`.
If they do not, the guard is blind and the same email can be reprocessed
into duplicate events indefinitely.

---

## Job status ownership

**`tasks.py` owns every DB write for job state. `pipeline.py` owns none.**

The task layer (`tasks.py`) writes all job state via three functions:

- `_set_processing(job)` — queued → processing
- `_set_terminal(job, outcome)` — any → done / needs_review / failed
- `_set_failed(job, reason, signature)` — exception path only (no outcome available)

`llm/pipeline.py` returns a `ProcessingOutcome` dataclass — it never touches
the database. After every pipeline call, the task reads the outcome and calls
`_set_terminal(job, outcome)`. The pipeline determines:

- `outcome.status` — `'done'`, `'needs_review'`, or `'failed'`
- `outcome.failure_reason` — which `REASON_*` code applies
- `outcome.notes` — user-visible explanation

**What must never happen:**

- Pipeline code calls `ScanJob.objects.filter(pk=...).update(status=...)`. That is a contract violation.
- A task sets `done` or `needs_review` directly — those come exclusively from `_set_terminal`.

This means: open `tasks.py` and you can read every job state transition end-to-end
without opening `pipeline.py`.

---

## Stale job recovery

A worker crash mid-task leaves a job stuck at `processing` forever with no
terminal status set. The `recover_stale_jobs` periodic task (runs every 10 minutes)
resets any job that has been in `processing` for longer than 10 minutes back to
`queued` so it can be re-enqueued.

---

## Job retention and cleanup

- `done` jobs are deleted after 1 day by `cleanup_events`.
- `needs_review` jobs are deleted after 30 days by `cleanup_events`.
- `failed` jobs are **never auto-deleted**. They remain visible in the user's queue
  until they are retried (automatically or manually) and complete, or until the admin
  dismisses them.
- Raw input fields (`file_b64`, `upload_text`, `upload_context`) are purged as soon
  as processing succeeds (done or needs_review). Only failed jobs retain them for retry.

---

## Retry contract

| Trigger | Jobs retried |
| --- | --- |
| `reset_monthly_scans` (1st of month) | All `failed` jobs with `reason=scan_limit` |
| `retry_jobs_after_plan_upgrade(user_id)` | All `failed` jobs for that user with `reason=scan_limit` or `reason=pro_required` |
| Admin bulk action | Any selected `failed` jobs via `_reenqueue_jobs` |
| User "Retry job" button | Single `failed` job with `reason=llm_error` or `reason=internal_error` |

A retry resets the job to `queued` and dispatches the appropriate task.
`failure_reason` and `failure_signature` are cleared on retry.

---

## Source types

| Source | Created by |
| --- | --- |
| `email` | Inbound email webhook |
| `upload` | Dashboard file upload, event_prompt_edit, or bulk reprocess |

There are exactly two sources. There is no `reprocess` source.

### Two kinds of reprocess — do not confuse them

**needs_review fix** (`reprocess_events` task, called from `queue_job_reprocess` view):

- User is on the job detail page reviewing pending events.
- Submits a correction prompt to fix the extraction.
- Mutates the original job — no new job is created.
- `job_pk` of the original job is always passed.
- Terminal status set by the pipeline.

**User-initiated re-extraction** (`process_text_as_upload` task, called from
`event_prompt_edit` or `events_bulk_action` views):

- User deletes one or more events from the dashboard and supplies a prompt.
- Not a fix of a needs_review job — the user just wants different events.
- Creates a new ScanJob with `source='upload'`.
- The view assembles the event data + prompt into a single text block before dispatch.
- The original events are deleted by the view (signal handles GCal) before the task runs.

If you see `reprocess_events` called without `job_pk`, or `process_text_as_upload`
called from the job queue page, something violated this contract.

---

## Rule system

Rules are owned by the user, not by categories. There are three types:

### Rule types

| Type | Purpose |
| --- | --- |
| `sender` | Match against the inbound email sender address |
| `keyword` | Match against the event title + description (substring, case-insensitive) |
| `prompt` | Inject custom instructions into the LLM prompt for matching emails |

### Matching logic (`resolve_category` in `llm/resolver.py`)

Rules are evaluated in this order for every extracted event:

1. **Sender rules** — `rule.pattern` is tested as a case-insensitive substring of the sender address. First match wins.
2. **Keyword rules** — `rule.pattern` is tested as a case-insensitive substring of `title + " " + description`. First match wins.
3. **LLM hint** — if no rule matched, use `category_hint` from extraction to find or create a category.

### Actions

- `categorize` — assign the rule's linked `category` to the event.
- `discard` — skip the event entirely; it is never saved.

### Prompt rules

A prompt rule with an empty `pattern` applies to all emails.
A prompt rule with a non-empty `pattern` applies only when `pattern` is a substring of the sender address.
Multiple matching prompt rules are concatenated (newline-joined) and injected before the extraction prompt.

### What must never happen ever

- A rule with `action='categorize'` and no `category` silently does nothing — it must be validated at save time.
- Rules are matched in `created_at` order within each type. Order is deterministic.
- Category deletion sets linked rules' `category` to NULL (`SET_NULL`) — the rule persists but becomes a no-op for categorize actions. The user should be warned or the rule cleaned up.
- Rules are never created or deleted via the category edit form. They are managed exclusively via the `/rules/` page.
