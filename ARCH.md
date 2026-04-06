# ARCH.md — NeverDue Codebase Architecture

> Per-file summary (≤10 lines each).

---

## Project Layout

```txt
neverdue/
├── accounts/           OAuth + user model + preferences
│   └── views/          auth, google, username, preferences, timezone
├── billing/            Stripe subscriptions
│   └── views/          pages, webhook
├── dashboard/          Events, categories, rules, GCal sync, iCal export
│   ├── gcal/           Google Calendar API (google-api-python-client)
│   ├── models/         Category, Rule, Event
│   └── views/          events, actions, categories, queue, rules, upload
├── emails/             Inbound email webhook, Procrastinate tasks, job queue
│   ├── tasks/          processing, reprocess, retry, scheduled, helpers
│   └── webhook/        resend API, parsing, user lookup
├── llm/                Anthropic extraction pipeline
│   ├── extractor/      prompts, client, text/image/email extraction, validation
│   └── pipeline/       orchestration, outcome, saving
├── support/            User support tickets → LLM triage → GitHub issues
└── project/            Django settings, URLs, static, templates
    ├── staff/          admin dashboard + bulk actions
    └── static/manual/
        ├── css/
        │   ├── base/        base.css, layout.css, forms.css
        │   ├── components/  house_ads.css
        │   └── pages/       per-page stylesheets
        └── js/
            ├── core/        base.js, forms.js
            └── pages/       per-page scripts
```

---

## File-by-File

### `accounts/models.py`

Custom `User` extends `AbstractUser` with Google OAuth tokens, timezone, language, `save_to_gcal` toggle (simple on/off, no token check), priority color preferences (stored as GCal colorId 1–11), and rolling monthly LLM token counters. `is_pro` delegates to `subscription.is_pro`. `MonthlyUsage` is an append-only snapshot written by `reset_monthly_scans` before clearing the rolling counters — stores pricing snapshot fields for cost recalculation.

### `accounts/views/` (package)

Split into 5 modules, re-exported from `__init__.py`:

- **`auth.py`** — `login_page`, `logout_view`. Thin wrappers around Django auth.
- **`google.py`** — Google OAuth2 flow: `google_login` builds the authorization URL, `google_callback` exchanges the code, fetches userinfo, upserts the User. Defines `SCOPES` and `_CLIENT_CONFIG`.
- **`username.py`** — `username_pick` view; validates and saves a chosen username.
- **`preferences.py`** — `preferences` view reads/writes all user settings in one POST (`save_to_gcal` is a simple toggle — no token required). `revoke_google` AJAX endpoint calls `revoke_google_token` and disables sync. Defines `GCAL_COLORS` palette (11 entries), `GCAL_COLOR_HEX` lookup, `LANGUAGES`, and `VALID_PRIORITY_COLOR_IDS`.
- **`timezone.py`** — Two AJAX endpoints (`set_timezone_auto`, `set_timezone_manual`) update timezone from browser JS. `VALID_TIMEZONES` set for validation.

### `accounts/utils.py`

Two functions: `get_valid_token(user)` checks token expiry with a 5-minute buffer; if stale, refreshes via `google-auth` and persists the new access token. Raises `ValueError` on missing refresh token or failed refresh. `revoke_google_token(user)` posts to Google's revocation endpoint and clears all token fields locally regardless of API outcome.

### `accounts/context_processors.py`

Injects `DOMAIN`, `ADSENSE_CLIENT_ID`, and `ADSENSE_SLOTS` into every template context.

### `accounts/urls.py`

Maps 9 URL patterns: login/logout, Google OAuth start/callback, username picker, preferences page, revoke-google AJAX endpoint, and the two timezone AJAX endpoints.

### `billing/models.py`

`Subscription` is a OneToOne to User with Stripe IDs, status (active/trialing/cancelled/past_due), and `current_period_end`. `is_pro` property returns True for active/trialing. No business logic beyond that property.

### `billing/views/` (package)

Split into 2 modules, re-exported from `__init__.py`:

- **`pages.py`** — `plans`, `checkout` (Stripe Checkout session with 7-day trial), `success`, `cancel`, `portal` (billing portal redirect). `_get_or_create_customer` helper.
- **`webhook.py`** — Stripe webhook handler. `_sync_subscription` updates local status and fires `retry_jobs_after_plan_upgrade` when status becomes active. `_handle_checkout_completed` attaches promo code discounts to subscriptions (Stripe trial+promo workaround). Verifies signature via `stripe.Webhook.construct_event`.

### `dashboard/models/` (package)

Split into 3 modules, re-exported from `__init__.py`. Each model sets `app_label = 'dashboard'` in Meta.

- **`category.py`** — `Category`: name, priority 1–4, GCal colorId, reminder JSON, hex color.
- **`rule.py`** — `Rule`: sender/keyword/prompt types with categorize/discard actions.
- **`event.py`** — `Event`: full calendar event with GCal ID, status active/pending, recurrence, scan_job FK. `clean()` validates end > start and recurrence constraints. `rrule` property generates RRULE string. `serialize_as_text` for reprocessing.

### `dashboard/gcal/` (package)

Google Calendar API via `google-api-python-client`. Re-exported from `__init__.py`:

- **`client.py`** — `_service(user)` builds a `googleapiclient` service using `Credentials(token=get_valid_token(user))`.
- **`crud.py`** — `delete_from_gcal`, `patch_event_color`. Both use `_service()` and catch `HttpError`.
- **`watch.py`** — `register_gcal_watch` / `stop_gcal_watch` manage GCal push notification channel lifecycle.
- **`signals.py`** — `event_pre_delete` receiver: calls `delete_from_gcal` unless `_skip_gcal_delete` is set.

### `dashboard/views/` (package)

Split into 6 modules, re-exported from `__init__.py`:

- **`events.py`** — `index`, `event_detail`, `event_edit`, `event_delete`.
- **`actions.py`** — `event_prompt_edit`, `events_bulk_action`, `export_events`. Handles re-extraction by serializing event data as text, deleting events, dispatching `process_text_as_upload`.
- **`categories.py`** — `categories`, `category_detail`, `category_edit`, `category_delete`, `categories_bulk_delete` (JSON POST, deletes by pk list).
- **`queue.py`** — `queue`, `queue_status` (paginated JSON for polling), `queue_job_detail`, `queue_job_reprocess`, `queue_job_retry`.
- **`rules.py`** — `rules`, `rule_add`, `rule_delete`.
- **`upload.py`** — `upload` view for file upload processing.

### `dashboard/writer.py`

`write_event_to_calendar`: single write path for all events. Deduplicates by (user, start, end). Splits into `_save_pending_event` (DB-only) and `_save_active_event` (GCal push + DB). When `save_to_gcal` is False, skips GCal and appends "Not synced to Google Calendar (disabled in Preferences)." to event description. `_build_gcal_body_from_dict` builds the API dict. `_resolve_color_id` picks color: event override → category GCal color → priority default. `GCalUnavailableError` raised when sync is on but push fails.

### `dashboard/ical.py`

`build_ics` produces RFC 5545 `.ics` bytes from a queryset using the `icalendar` library. Maps Category.priority to RFC 5545 PRIORITY values. `_parse_rrule` converts the stored RRULE string to a dict with datetime-typed UNTIL.

### `dashboard/tasks.py`

Single Procrastinate task: `patch_category_colors`. Runs async after a category color change; patches all active, uncolored events in that category via `patch_event_color`.

### `dashboard/webhook.py`

`gcal_webhook` (CSRF-exempt POST) receives GCal push notifications. Uses `dashboard.gcal.client._service` for API calls. Validates channel ID against User, syncs changed events, self-renews push channel if expiry is within 2 days.

### `dashboard/templatetags/tz_display.py`

Single template filter `in_user_tz` to convert a UTC datetime to the user's preferred timezone for display.

### `emails/models.py`

`ScanJob` tracks every email/upload processing attempt: status (queued/processing/needs_review/done/failed), source (email/upload), failure reason codes (`REASON_*` constants), serialized `task_args` for replay on retry. `duration_seconds` property computes wall-clock time.

### `emails/tasks/` (package)

Split into 5 modules, re-exported from `__init__.py`:

- **`helpers.py`** — `_transient_retry`, `_check_sender_rules`, `_load_user`, `_apply_outcome` (writes done/needs_review/failed from `ProcessingOutcome`; purges `file_b64`/`upload_text`/`upload_context` on non-failed outcomes), `track_llm_usage`.
- **`processing.py`** — `process_inbound_email` (fetches full email from Resend inside the task), `process_uploaded_file`, `process_text_as_upload`. All follow: retrieve job → set processing → call pipeline → apply outcome.
- **`reprocess.py`** — `reprocess_events`: serializes pending events, calls `process_text`, deletes originals only on success.
- **`scheduled.py`** — Periodic tasks: `reset_monthly_scans` (1st of month), `recover_stale_jobs` (every 10 min), `cleanup_events` (2am UTC), `cleanup_old_tickets` (3am UTC — deletes support tickets older than 30 days).
- **`retry.py`** — `retry_jobs_after_plan_upgrade`, `_retry_failed_jobs`, `_retry_jobs`. Re-enqueues failed jobs by reading `task_args`.

### `emails/views.py`

Single entry point: `inbound` (Resend webhook — verifies Svix signature, resolves user, creates ScanJob, dispatches task with `email_id` only).

### `emails/webhook/` (package)

Split into 3 modules, re-exported from `__init__.py`:

- **`resend.py`** — `fetch_full_email`, `fetch_attachment_content`, `verify_resend_signature` (Svix HMAC-SHA256), `SUPPORTED_ATTACHMENT_TYPES`.
- **`parsing.py`** — `extract_email_text` (plain text preferred, HTML stripped as fallback), `extract_attachments` (returns `[b64, content_type, filename]` triples).
- **`users.py`** — `get_user_from_recipient`, `RESERVED_USERNAMES`.

### `llm/extractor/` (package)

Split into 7 modules, re-exported from `__init__.py`:

- **`prompts.py`** — `SYSTEM_PROMPT` (extraction instructions) and `RECONCILIATION_PROMPT` (merge step).
- **`client.py`** — Anthropic client setup, `call_api` wrapper, and `LLMAPIError` exception (distinguishes API failures from parse errors).
- **`utils.py`** — `is_informative_filename`, `get_tz`, `today_in_tz`, junk filename detection.
- **`validation.py`** — `parse_and_validate`, `_validate_event`: sanitises LLM output, converts local→UTC, auto-fixes past-year dates (bumps to current year and promotes to active if the LLM marked pending due to past date), strips invalid recurrence, ensures pending events have a concern. `VALID_FREQS`, `RECURRENCE_MIN_INTERVAL_DAYS`.
- **`text.py`** — `extract_events(text)`: single LLM call for plain text input.
- **`image.py`** — `extract_events_from_image`: handles image/PDF via base64-encoded content blocks.
- **`email.py`** — `extract_events_from_email`: two-step pipeline (extract per-attachment, then reconcile with body). Skips reconciliation when all attachments are visual and events were already extracted — context is already fed to each per-image call, so reconciliation would only risk losing events. Supports visual and non-visual attachments.

### `llm/pipeline/` (package)

Split into 3 modules, re-exported from `__init__.py`:

- **`outcome.py`** — `ProcessingOutcome` dataclass (created, notes, status, failure_reason).
- **`entry.py`** — `process_text`, `process_email`: public entry points that return `ProcessingOutcome`. Catches `LLMAPIError` → `failed/llm_error`, `GCalUnavailableError` → `failed/gcal_disconnected`. When all events are discarded by rules, returns `done` with descriptive notes instead of `needs_review`. No direct DB writes — delegates to `saving.py`.
- **`saving.py`** — `_check_and_increment_scans` (atomic `F()`-based UPDATE), `_fire_usage` (async token tracking), `_save_events` (conflict detection, all-or-nothing pending rule, category resolution via `resolve_category`, discard tracking, writes via `write_event_to_calendar`; returns `(created, has_pending, discarded)`), `_find_conflicts`, `_append_conflict_concern`, `_get_or_create_uncategorized`.

### `llm/resolver.py`

`resolve_category` applies user rules in priority order: sender rules → keyword rules → hint-matched existing category → hint-created new category → None. `collect_prompt_injections` gathers applicable prompt-type rules. `_infer_priority` maps category hint keywords to priority levels (1–4).

### `support/models.py`

`Ticket` model with UUID primary key. Fields: `user` (FK to User, SET_NULL), `type` (bug/feature/howto/perf/privacy — default `bug`, overwritten by LLM triage), `body`, `llm_answer` (populated for howto and privacy tickets before user sees result), `gh_url`, `status` (pending/awaiting_user/open/closed), `created_at`, `updated_at`. Ordered by `-created_at`. Module-level `CONTACT_SERVICES` dict maps service keys (privacy, billing, legal, abuse) to email prefixes for `{key}@service.neverdue.ca`.

### `support/llm.py`

Single public function `triage(body)` — one Anthropic API call that classifies the ticket type and produces all downstream output simultaneously: a plain-text `answer` for howto tickets, or `(title, body, labels)` for a GitHub issue for all other types. Privacy tickets return null for all issue fields (handled outside LLM). Lazy-loads ARCH.md once as context. Returns a dict with keys `type`, `answer`, `title`, `body`, `labels`. `VALID_TYPES` and `VALID_LABELS` guard against invalid LLM output. Reuses `llm.extractor.client.call_api` — no new Anthropic client.

### `support/github.py`

`create_issue(title, body, labels)` opens a GitHub issue on `Vic-Nas/neverdue` via the GitHub REST API using `settings.GITHUB_TOKEN`. `verify_github_signature(body, signature)` validates `X-Hub-Signature-256` HMAC against `settings.GITHUB_WEBHOOK_SECRET`. Uses `httpx`. Returns issue `html_url`. Raises `ValueError` if token is unconfigured.

### `support/tasks.py`

Single Procrastinate task `process_ticket(ticket_id)`. Calls `triage(body)` once, then branches: howto → save answer → `STATUS_AWAITING`; privacy → set `llm_answer` to contact address from `CONTACT_SERVICES` → `STATUS_AWAITING` (no GitHub, no email); all others → `create_issue` → `STATUS_OPEN`. Always persists the LLM-determined type. Catches `LLMAPIError` and unexpected exceptions with logging.

### `support/views.py`

Five login-required views plus one public webhook: `submit`; `ticket_detail`; `resolve` (AJAX — satisfied closes ticket, unsatisfied calls `triage` + `create_issue`); `my_tickets`; `github_webhook` (CSRF-exempt POST — verifies `X-Hub-Signature-256`, handles `closed` issue events, updates matching ticket to `STATUS_CLOSED` by `gh_url`).

### `support/urls.py`

`app_name = "support"`. Five patterns: `""` → `submit`, `"tickets/"` → `my_tickets`, `"<uuid:pk>/"` → `ticket_detail`, `"<uuid:pk>/resolve/"` → `resolve`, `"gh-webhook/"` → `github_webhook`.

### `project/settings.py`

Standard Django config. `django.contrib.admin` removed (replaced by `/staff/`). `AUTH_USER_MODEL = 'accounts.User'`. Procrastinate uses Postgres (no Redis). Static files via WhiteNoise. All credentials from env. `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET` used by support app. Procrastinate, httpx, and httpcore loggers set to WARNING to avoid dumping task args and request bodies at DEBUG.

### `project/views.py`

Three passthrough views: `privacy`, `terms`, `help_page` — each renders a static template.

### `project/staff/` (package)

Split into 2 modules, re-exported from `__init__.py`:

- **`dashboard.py`** — `staff_dashboard` behind `@staff_required` decorator. Aggregates ScanJob and MonthlyUsage stats, builds 3 Chart.js datasets (volume by status, failure rate, failure reason breakdown). Defines `staff_required`, `_cost`, `_date_range`, `FAILURE_COLORS`.
- **`actions.py`** — `staff_retry_jobs`, `staff_retry_single`, `staff_delete_single`, `staff_bulk_retry`, `staff_bulk_delete`. `_parse_pks` handles JSON and form POST.

### `project/urls.py`

Imports `project.staff` as `staff_views` and `project.views` as `views`. Staff routes under `/staff/`, app routes delegated to each app's `urls.py`. Support app mounted at `/support/`.

### `project/templates/base.html`

Base layout with top nav (auth-aware links, queue badges, Pro/Upgrade indicator), ad sidebar, and CSRF meta tag. Injects `js/core/base.js` for all authenticated users.

### `project/templates/dashboard/` (13 templates)

All extend `base.html`. `index.html` renders event card grid with bulk-select. `queue_job_detail.html` renders reprocess form and retry button. `rules.html` renders three rule-type forms.

### `project/templates/support/` (3 templates)

- **`submit.html`** — Bare textarea + submit button. No type picker; LLM classifies on submission.
- **`ticket_detail.html`** — Status-aware: spinner if pending; for `awaiting_user` shows LLM answer — howto tickets show Yes/No resolve buttons, privacy tickets show contact message only; GitHub link if open/closed.
- **`my_tickets.html`** — Card list matching categories/rules aesthetic; type badge colour-coded by LLM-assigned type.

### `project/static/manual/css/base/`

- **`base.css`** — Design tokens, global resets, typography, button variants, input styles, messages, footer, badge animations.
- **`layout.css`** — Top nav, sidebar layout, responsive breakpoints, ad sidebar positioning.
- **`forms.css`** — Form groups, labels, hints, actions row, reminder/rule rows.

### `project/static/manual/css/pages/`

Per-page stylesheets: `auth.css`, `billing.css`, `categories.css`, `dashboard.css`, `email-inbox.css`, `events.css`, `login.css`, `preferences.css`, `queue.css`, `staff.css`, `support.css`, `upload.css`.

### `project/static/manual/css/components/`

- **`house_ads.css`** — Fallback ad card styles (Railway/Koho).

### `project/static/manual/js/core/`

- **`base.js`** — Timezone auto-detect, hamburger menu, global queue badge polling (5s). Exposes `window.neverdue.startQueuePolling`.
- **`forms.js`** — `toggleDependentGroup`, `addDynamicRow`, recurrence toggle, color label sync. `addReminder()` and `addRule()` exposed globally.

### `project/static/manual/js/pages/`

- **`dashboard.js`** — Bulk selection mode for event grid, export URL building, bulk delete/reprocess.
- **`categories.js`** — Select mode for category cards, bulk delete via JSON POST.
- **`event_edit.js`** — Prompt-edit flow on existing event.
- **`preferences.js`** — Auto-delete toggle, GCal color swatch radio group, smart Google permissions button (Revoke → Restore swap after disconnect).
- **`queue.js`** — Client-side queue table renderer, polls `queue_status` every 4s.
- **`queue_action.js`** — Reprocess button (needs_review) and retry button (failed) in one IIFE.
- **`rules.js`** — AJAX add/delete rules for all three rule types.
- **`support.js`** — Resolve flow for howto tickets: disables Yes/No buttons on click, POSTs to `support:resolve`, replaces block with result on success, re-enables on error.
- **`upload.js`** — Drag-and-drop file input enhancement.

---