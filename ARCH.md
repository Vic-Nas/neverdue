# ARCH.md — NeverDue Codebase Architecture

> Per-file summary (≤10 lines each).

---

## Project Layout

```txt
neverdue/
├── accounts/           OAuth + user model + preferences
│   └── views/          auth, google, username, preferences, timezone
├── billing/            Stripe subscriptions, coupons, referrals
│   ├── tests/          coupon_workflow, discount_stack, referral_workflow, subscription_workflow
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

Custom `User` extends `AbstractUser` with Google OAuth tokens, timezone, language, `save_to_gcal` toggle, priority color preferences (stored as GCal colorId 1–11), rolling monthly LLM token counters, and a `referred_by` FK (nullable self-reference set at checkout when a referral code is used). `is_pro` delegates to `subscription.is_pro`. `MonthlyUsage` is an append-only snapshot written by `reset_monthly_scans`.

### `accounts/views/` (package)

Split into 5 modules, re-exported from `__init__.py`:

- **`auth.py`** — `login_page`, `logout_view`. Thin wrappers around Django auth.
- **`google.py`** — Google OAuth2 flow: `google_login` builds the authorization URL, `google_callback` exchanges the code, fetches userinfo, upserts the User. Defines `SCOPES` and `_CLIENT_CONFIG`.
- **`username.py`** — `username_pick` view; validates and saves a chosen username; redirects to `billing:membership` after signup.
- **`preferences.py`** — `preferences` view reads/writes all user settings in one POST. `revoke_google` AJAX endpoint calls `revoke_google_token`. Defines `GCAL_COLORS`, `GCAL_COLOR_HEX`, `LANGUAGES`, `VALID_PRIORITY_COLOR_IDS`.
- **`timezone.py`** — Two AJAX endpoints (`set_timezone_auto`, `set_timezone_manual`). `VALID_TIMEZONES` set for validation.

### `accounts/utils.py`

Two functions: `get_valid_token(user)` checks token expiry with a 5-minute buffer, refreshes via `google-auth` if stale, raises `ValueError` on missing refresh token. `revoke_google_token(user)` posts to Google's revocation endpoint and clears all token fields regardless of API outcome.

### `accounts/context_processors.py`

Injects `DOMAIN`, `ADSENSE_CLIENT_ID`, and `ADSENSE_SLOTS` into every template context.

### `accounts/urls.py`

Maps 9 URL patterns: login/logout, Google OAuth start/callback, username picker, preferences page, revoke-google AJAX endpoint, and the two timezone AJAX endpoints.

### `billing/models.py`

- **`Subscription`** — OneToOne to User. Stripe IDs, status (active/trialing/cancelled/past_due), `current_period_end`, and `referral_code` (nullable unique NVD-XXXXX string). `is_pro` returns True for active/trialing. `generate_referral_code()` lazily creates a code and calls `_sync_referral_code_to_stripe`. `_sync_referral_code_to_stripe` pushes the code to Stripe as a Coupon (12% display) + PromotionCode via `_stripe_upsert_coupon` / `_stripe_ensure_promotion_code`.
- **`Coupon`** — Staff-created percent discount (code, percent, label, expires_at). `sync_to_stripe()` upserts the Stripe Coupon and ensures a PromotionCode exists. `is_redeemable()` checks expiry.
- **`CouponRedemption`** — Records a user redeeming a Coupon. `unique_together (user, coupon)` prevents double-counting. `on_delete=PROTECT` on the Coupon FK — a redeemed coupon cannot be deleted.
- **`_stripe_upsert_coupon`** — Delete-then-create a Stripe Coupon by ID (idempotent). Stripe auto-reactivates existing PromotionCodes after recreation.
- **`_stripe_ensure_promotion_code`** — Creates an active PromotionCode only if none exists (`active=True` filter).

### `billing/discount.py`

Pure discount computation — no side effects, no Stripe calls. `compute_discount(user)` sums active `CouponRedemption` percents plus `12.5 * active_referral_count`, caps at 100, returns `int`. `_count_active_referrals(user)` counts `referred_by=user` users with `subscription__status='active'` (trialing excluded). `referral_summary(user)` returns masked-email dicts for display on the billing page. `_mask_email` produces `jo***e@gm***.com` format.

### `billing/views/` (package)

- **`pages.py`** — Renders `billing/membership.html` (replaced `plans.html`). Injects `show_referral` context var to conditionally display the referral section. `generate_referral_code` view lazily creates a referral code on demand.
- **`webhook.py`** — Stripe webhook handler (signature verified via `stripe.Webhook.construct_event`, API pinned to `2024-06-20`):
  - `_sync_subscription` — updates local status/period; on `trialing→active` transition fires `retry_jobs_after_plan_upgrade`, `_push_combined_discount`, and `_shift_referrer_billing_anchor`.
  - `_handle_checkout_completed` — resolves each checkout discount to its coupon ID via `PromotionCode.retrieve`; if it matches a `Subscription.referral_code`, extends B's trial to 30 days (`proration_behavior='none'`) and strips the percent coupon (`discounts=[]`); non-referral coupons are attached normally.
  - `_handle_discount_created` — sets `referred_by` on B's user when a referral code coupon fires, or records a `CouponRedemption` for staff coupons.
  - `_handle_invoice_upcoming` — calls `_push_combined_discount` ~1 hour before each billing cycle.
  - `_shift_referrer_billing_anchor` — when B goes `trialing→active`, if `B.current_period_end > A.current_period_end`, sets A's `trial_end` to `B.current_period_end + 1 day` with `proration_behavior='none'`; updates local `referrer_sub.current_period_end` so subsequent referrals compare against the already-shifted date.
  - `_push_combined_discount` — computes discount via `compute_discount`, deletes/recreates `nvd-auto-<pk>` coupon on Stripe, applies it with `duration='once'`; removes discount if percent is 0.

### `billing/urls.py`

`app_name = 'billing'`. Patterns: `membership/` (name=`membership`), `checkout/`, `success/`, `cancel/`, `portal/`, `webhook/`, `referral-code/generate/`.

### `billing/tests/` (package)

- **`helpers.py`** — `BillingTestCase` (tracks Stripe objects for cleanup), `make_user`, `create_stripe_customer`, `create_stripe_subscription`, `s()` (Stripe test-mode client).
- **`test_coupon_workflow.py`** — Real-Stripe tests: `CouponSyncCreatesPromotionCode` (sync creates coupon + promo code, second sync is idempotent), `CouponAdminDeleteOrphansStripe`, `DiscountCreatedWebhookRecordsRedemption` (referral path, staff coupon path, unknown coupon ignored), `PushCombinedDiscountAppliesCorrectly`.
- **`test_discount_stack.py`** — DB-level and real-Stripe stacking tests: `CouponReferralStack` (coupon + N referrals, cap at 100, trialing excluded), `CouponRedemptionIntegrity` (unique_together, PROTECT delete, admin correction), `StripeDiscountSync` (DB discount matches Stripe-applied percent for all combinations).
- **`test_referral_workflow_real.py`** — Real-Stripe referral flow: `ReferralCodeExistsOnStripe` (generate_referral_code syncs to Stripe as coupon + promo code), `ReferralWebhookSetsReferredBy` (discount.created sets referred_by, not overwritten), `ReferralDiscountComputation` (12.5% per active referral, int truncation, cap).
- **`test_subscription_workflow.py`** — Core subscription lifecycle tests.

### `dashboard/models/` (package)

Split into 3 modules, re-exported from `__init__.py`. Each model sets `app_label = 'dashboard'` in Meta.

- **`category.py`** — `Category`: name, priority 1–4, GCal colorId, reminder JSON, hex color.
- **`rule.py`** — `Rule`: sender/keyword/prompt types with categorize/discard actions.
- **`event.py`** — `Event`: full calendar event with GCal ID, status active/pending, recurrence, scan_job FK. Supports a `links` JSONField (list of `{url, title}` dicts). `clean()` validates end > start and recurrence constraints. `rrule` property generates RRULE string. `serialize_as_text` and `__str__` include links.

### `dashboard/views/` (package)

Split into 6 modules, re-exported from `__init__.py`:

- **`events.py`** — `index`, `event_detail`, `event_edit`, `event_delete`. Event edit supports multiple links (URL + label) via form fields and `_parse_links` helper.
- **`actions.py`** — `event_prompt_edit`, `events_bulk_action`, `export_events`.
- **`categories.py`** — `categories`, `category_detail`, `category_edit`, `category_delete`, `categories_bulk_delete`.
- **`queue.py`** — `queue`, `queue_status` (paginated JSON for polling), `queue_job_detail`, `queue_job_reprocess`, `queue_job_retry`.

### `dashboard/writer.py`

`write_event_to_calendar`: single write path for all events. Deduplicates by (user, start, end). Splits into `_save_pending_event` (DB-only) and `_save_active_event` (GCal push + DB). `_build_gcal_body_from_dict` builds the API dict, including the first link as GCal source and extras in the description. Saves the `links` field.

### `dashboard/ical.py`

`build_ics` produces RFC 5545 `.ics` bytes from a queryset using the `icalendar` library. Maps Category.priority to RFC 5545 PRIORITY values. First event link exported as the event URL. `_parse_rrule` converts the stored RRULE string to a dict with datetime-typed UNTIL.

### `dashboard/gcal/` (package)

Google Calendar API via `google-api-python-client`. Re-exported from `__init__.py`:

- **`client.py`** — `_service(user)` builds a `googleapiclient` service using `Credentials(token=get_valid_token(user))`.

### `dashboard/tasks.py`

Single Procrastinate task: `patch_category_colors`. Runs async after a category color change; patches all active, uncolored events in that category via `patch_event_color`.

### `dashboard/webhook.py`

`gcal_webhook` (CSRF-exempt POST) receives GCal push notifications. Validates channel ID against User, syncs changed events, self-renews push channel if expiry is within 2 days.

### `dashboard/templatetags/tz_display.py`

Single template filter `in_user_tz` to convert a UTC datetime to the user's preferred timezone for display.

### `emails/models.py`

`ScanJob` tracks every email/upload processing attempt: status (queued/processing/needs_review/done/failed), source (email/upload), failure reason codes (`REASON_*`), serialized `task_args` for replay on retry. `duration_seconds` property computes wall-clock time.

### `emails/tasks/` (package)

Split into 5 modules, re-exported from `__init__.py`:

- **`helpers.py`** — `_transient_retry`, `_check_sender_rules`, `_load_user`, `_apply_outcome`, `track_llm_usage`.
- **`processing.py`** — `process_inbound_email`, `process_uploaded_file`, `process_text_as_upload`.
- **`reprocess.py`** — `reprocess_events`: serializes pending events, calls `process_text`, deletes originals only on success.
- **`scheduled.py`** — `reset_monthly_scans` (1st of month), `recover_stale_jobs` (every 10 min), `cleanup_events` (2am UTC), `cleanup_old_tickets` (3am UTC).
- **`retry.py`** — `retry_jobs_after_plan_upgrade`, `_retry_failed_jobs`, `_retry_jobs`.

### `emails/views.py`

Single entry point: `inbound` (Resend webhook — verifies Svix signature, resolves user, creates ScanJob, dispatches task with `email_id` only).

### `emails/webhook/` (package)

- **`resend.py`** — `fetch_full_email`, `fetch_attachment_content`, `verify_resend_signature`, `SUPPORTED_ATTACHMENT_TYPES`.
- **`parsing.py`** — `extract_email_text`, `extract_attachments`.
- **`users.py`** — `get_user_from_recipient`, `RESERVED_USERNAMES`.

### `llm/extractor/` (package)

Split into 7 modules, re-exported from `__init__.py`:

- **`prompts.py`** — `SYSTEM_PROMPT` (extraction instructions, requires links array) and `RECONCILIATION_PROMPT` (merge step).
- **`client.py`** — Anthropic client setup, `call_api` wrapper, `LLMAPIError` exception.
- **`utils.py`** — `is_informative_filename`, `get_tz`, `today_in_tz`, junk filename detection.
- **`validation.py`** — `parse_and_validate`, `_validate_event`: sanitises LLM output, converts local→UTC, auto-fixes past-year dates, strips invalid recurrence, normalizes links. `VALID_FREQS`, `RECURRENCE_MIN_INTERVAL_DAYS`.
- **`text.py`** — `extract_events(text, ..., existing_categories=None)`: injects user's category names into the LLM prompt.
- **`image.py`** — `extract_events_from_image`: handles image/PDF via base64-encoded content blocks.
- **`email.py`** — `extract_events_from_email(..., existing_categories=None)`: same category grounding logic as `text.py`.

### `llm/pipeline/` (package)

Split into 3 modules, re-exported from `__init__.py`:

- **`outcome.py`** — `ProcessingOutcome` dataclass (created, notes, status, failure_reason).
- **`entry.py`** — `process_text`, `process_email`: fetch user's category names, pass to extractors. Returns `ProcessingOutcome`. No direct DB writes.
- **`saving.py`** — `_check_and_increment_scans` (atomic `F()`-based UPDATE), `_fire_usage`, `_save_events` (conflict detection, all-or-nothing pending rule, category resolution, discard tracking, writes via `write_event_to_calendar`).

### `llm/resolver.py`

`resolve_category` applies user rules in priority order: sender → keyword → hint-matched existing → hint-created new → None. `collect_prompt_injections` gathers prompt-type rules. `_infer_priority` maps category hint keywords to priority levels 1–4.

### `support/models.py`

`Ticket` with UUID PK. Fields: `user` (FK SET_NULL), `type` (bug/feature/howto/perf/privacy), `body`, `llm_answer`, `gh_url`, `status` (pending/awaiting_user/open/closed), timestamps. `CONTACT_SERVICES` maps service keys to email prefixes.

### `support/llm.py`

`triage(body)` — one Anthropic API call: classifies ticket type and produces `answer` (howto) or `(title, body, labels)` for GitHub issue. Lazy-loads ARCH.md once as context. Reuses `llm.extractor.client.call_api`.

### `support/github.py`

`create_issue(title, body, labels)` opens a GitHub issue via REST API using `settings.GITHUB_TOKEN`. `verify_github_signature` validates `X-Hub-Signature-256` HMAC. Uses `httpx`.

### `support/tasks.py`

`process_ticket(ticket_id)`: howto → save answer → `STATUS_AWAITING`; privacy → contact address → `STATUS_AWAITING` (no GitHub); others → `create_issue` → `STATUS_OPEN`.

### `support/views.py`

`submit`, `ticket_detail`, `resolve` (AJAX), `my_tickets`, `github_webhook` (CSRF-exempt — verifies signature, handles `closed` issue events).

### `support/urls.py`

`app_name = "support"`. Five patterns: submit, my_tickets, ticket_detail, resolve, gh-webhook.

### `project/settings.py`

Standard Django config. `django.contrib.admin` removed. `AUTH_USER_MODEL = 'accounts.User'`. Procrastinate uses Postgres. Static files via WhiteNoise. `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID` from env.

### `project/views.py`

Three passthrough views: `privacy`, `terms`, `help_page`.

### `project/staff/` (package)

- **`dashboard.py`** — `staff_dashboard` behind `@staff_required`. Aggregates ScanJob and MonthlyUsage stats, builds 3 Chart.js datasets. Defines `staff_required`, `_cost`, `_date_range`, `FAILURE_COLORS`.
- **`actions.py`** — `staff_retry_jobs`, `staff_retry_single`, `staff_delete_single`, `staff_bulk_retry`, `staff_bulk_delete`. `_parse_pks` handles JSON and form POST.

### `project/urls.py`

Staff routes under `/staff/`, app routes delegated to each app's `urls.py`. Support app mounted at `/support/`.

### `project/templates/base.html`

Base layout with top nav (auth-aware links, queue badges, Pro/Upgrade indicator, Referral link), ad sidebar, and CSRF meta tag. Username span removed from nav. Injects `js/core/base.js` for all authenticated users.

### `project/templates/billing/membership.html`

Replaced `plans.html`. Renders plan options and, when `show_referral` is True, the referral section (code generation, referral summary, masked referred-user list). Checkout link via `billing:checkout`.

### `project/templates/dashboard/` (13 templates)

All extend `base.html`. `event_edit.html` supports adding/removing multiple links. `event_detail.html` displays all event links. `index.html` renders event card grid with bulk-select. `queue_job_detail.html` renders reprocess form and retry button. `rules.html` renders three rule-type forms. `categories.html` points to `billing:membership`.

### `project/templates/billing/cancel.html`

Points to `billing:membership` (no longer references `billing:plans`).

### `project/templates/accounts/login.html`

Includes a referral feature block: "Use Pro for free with referrals".

### `project/templates/help/help.html`

Includes a Referrals section explaining how the referral program works.

### `project/templates/support/` (3 templates)

- **`submit.html`** — Textarea + submit. LLM classifies on submission.
- **`ticket_detail.html`** — Status-aware: spinner if pending; LLM answer for `awaiting_user`; GitHub link if open/closed.
- **`my_tickets.html`** — Card list; type badge colour-coded by LLM-assigned type.

### `project/static/manual/css/base/`

- **`base.css`** — Design tokens, global resets, typography, button variants, input styles, messages, footer, badge animations.
- **`layout.css`** — Top nav, sidebar layout, responsive breakpoints, ad sidebar positioning.
- **`forms.css`** — Form groups, labels, hints, actions row, reminder/rule rows.

### `project/static/manual/css/pages/`

Per-page stylesheets: `auth.css`, `billing.css`, `categories.css`, `dashboard.css`, `email-inbox.css`, `events.css`, `login.css`, `preferences.css`, `queue.css`, `staff.css`, `support.css`, `upload.css`.

### `project/static/manual/css/components/`

- **`house_ads.css`** — Fallback ad card styles.

### `project/static/manual/js/core/`

- **`base.js`** — Timezone auto-detect, hamburger menu, global queue badge polling (5s). Exposes `window.neverdue.startQueuePolling`.
- **`forms.js`** — `toggleDependentGroup`, `addDynamicRow`, recurrence toggle, color label sync. `addReminder()` and `addRule()` exposed globally.

### `project/static/manual/js/pages/`

- **`dashboard.js`** — Bulk selection mode for event grid, export URL building, bulk delete/reprocess.
- **`categories.js`** — Select mode for category cards, bulk delete via JSON POST.
- **`event_edit.js`** — Prompt-edit flow on existing event. Handles dynamic add/remove for event links.
- **`preferences.js`** — Auto-delete toggle, GCal color swatch radio group, smart Google permissions button.
- **`queue.js`** — Client-side queue table renderer, polls `queue_status` every 4s.
- **`queue_action.js`** — Reprocess button (needs_review) and retry button (failed) in one IIFE.
- **`rules.js`** — AJAX add/delete rules for all three rule types.
- **`support.js`** — Resolve flow for howto tickets: disables Yes/No buttons, POSTs to `support:resolve`, re-enables on error.
- **`upload.js`** — Drag-and-drop file input enhancement.

---
