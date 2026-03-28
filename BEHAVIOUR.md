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
                    → failed         (unhandled exception)

needs_review → processing            (user submits a reprocess prompt)
             → done                  (user explicitly cancels with no prompt)
```

### Status definitions

| Status | Meaning |
| --- | --- |
| `queued` | Job created, not yet picked up by a worker |
| `processing` | Worker is actively running |
| `done` | All events created are active; nothing left to do |
| `needs_review` | One or more events are `pending`; user must review and resubmit |
| `failed` | An unhandled exception aborted the job |

### What must never happen

- A job with pending events is marked `done`. That is a lie.
- A job is marked `done` with zero events and no note explaining why.
- A reprocess creates a new ScanJob. There is one job per source input.
- Two jobs race to write events for the same source.

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

This rule applies at save time in `_save_events`, not at extraction time.

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
4. Frontend calls `reprocess_events.delay(user_id, event_ids, prompt, job_pk=job.pk)`.
5. Worker sets job status → `processing`.
6. Worker reads `source_email_id` from the pending events **before** deleting them.
7. Worker deletes the pending events.
8. Worker calls `process_text(user, prompt, source_email_id=preserved_id, scan_job=job)`.
9. `_save_events` writes new events linked to the **same** job.
10. If all new events are active → job → `done`.
11. If any new events are pending → job → `needs_review` (user acts again).

### What must never happen during reprocess

- `source_email_id` is lost. Future identical emails would bypass dedup.
- A new ScanJob is created. The original job is the permanent record.
- The original job ends up with zero events and no explanation.
- Job status is set to `done` by the task — the pipeline owns that decision.

---

## Conflict detection

When `_save_events` processes a pending event, it checks for conflicts against
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

`process_inbound_email` checks before processing:

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

**The pipeline (`_save_events`) owns the terminal job status decision.**

The task layer (`tasks.py`) is responsible for:

- Creating the job
- Setting `processing` when work begins
- Setting `failed` on unhandled exceptions

The task layer must NOT set `done` or `needs_review` — those are set by
`_save_events` based on what was actually created.

This single rule eliminates the class of bugs where a task marks a job `done`
before, after, or instead of what the pipeline actually produced.

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
