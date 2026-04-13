# ARCH.md — NeverDue Codebase Architecture

> Per-file summary (≤10 lines each).

---

## Project Layout

```txt
neverdue/
├── accounts/           OAuth + user model + preferences
│   └── views/          auth, google, username, preferences, timezone
├── billing/            Stripe subscriptions, coupons, referrals
│   ├── tests/          models, signals, tasks, views, integration (see TESTS.md)
│   └── views/          pages
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

Custom `User` extends `AbstractUser` with Google OAuth tokens, timezone, language, `save_to_gcal` toggle, priority color preferences (stored as GCal colorId 1–11), and rolling monthly LLM token counters. `is_pro` delegates to `subscription.is_pro`. `MonthlyUsage` is an append-only snapshot written by `reset_monthly_scans`.

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

- **`Coupon`** — Staff-created discount coupon pushed to Stripe on first save. Fields: `code` (unique), `percent`, `max_redemptions` (nullable — unlimited if blank, enforced by Stripe at checkout), `head` (nullable FK to User — the user who earns a head refund each month; null = NeverDue grant, always pays out), `stripe_coupon_id`, `stripe_promotion_code_id`, `created_at`. `save()` calls `_push_to_stripe()` on creation: creates a Stripe Coupon (`nvd-<code>`, forever) then a PromotionCode with the same human-readable code. Once created, code/percent/max_redemptions/head are immutable (admin enforces via `get_readonly_fields`).
- **`CouponRedemption`** — Records that a user subscribed using a coupon. FK to `Coupon` and FK to `User`, `unique_together`. Created by the `customer.discount.created` webhook; deleted when the user unsubscribes. A user may hold redemptions on multiple coupons across subscription cycles.
- **`Subscription`** — OneToOne to User. Stripe IDs, status (active/trialing/cancelled/past_due), `current_period_end`, `created_at`. `referral_coupon` OneToOne FK to the user's personal referral `Coupon` (head=user, percent=12.5, max_redemptions=12); null until generated. `referral_code` property delegates to `referral_coupon.code`. `is_pro` returns True for active/trialing. `generate_referral_code()` lazily creates the `Coupon` row (which triggers `_push_to_stripe`) and links it. Idempotent.
- **`RefundRecord`** — Idempotency guard for monthly refunds. Two partial unique constraints: `(redemption, stripe_invoice_id)` for redeemer refunds and `(coupon_head, stripe_invoice_id)` for head refunds. `on_delete=PROTECT` on both FKs.
- **`compute_discount(user)`** — Display-only. Redeemer side: flat `percent` per redemption if head is active (or null). Head side: `percent × active-redeemer-count` per coupon they head. Sums both sides, caps at 100, returns `math.ceil` int.

### `billing/apps.py`

`BillingConfig.ready()` imports `billing.signals` to register all dj-stripe signal handlers at startup.

### `billing/signals.py`

dj-stripe signal handlers — all Stripe object syncing is handled by dj-stripe automatically. Only NeverDue business logic lives here. Registered via three `WEBHOOK_SIGNALS[event_type].connect()` calls, each wrapped in `_wrap()` which catches and re-raises so Stripe gets a 500 and retries.

- **`handle_customer_discount_created`** — Resolves the `Coupon` by code (from `PromotionCode.code` on the event, or derived from Stripe Coupon ID `nvd-<code>` as fallback). Guards: unknown code (skip silently), self-referral on a referral coupon (strip Stripe discount via `Customer.delete_discount`), duplicate redemption (skip). Creates `CouponRedemption(coupon, user)` on success.
- **`handle_subscription_updated`** — Defers `retry_jobs_after_plan_upgrade` on any→active transition.
- **`handle_subscription_cancelled`** — Fires on `customer.subscription.deleted`. Deletes all `CouponRedemption` rows for the cancelled user. The `Coupon` rows themselves are untouched — other redeemers and the head are unaffected. If the user resubscribes they enter a fresh code at checkout.

### `billing/tasks.py`

Single Procrastinate task: `process_monthly_refunds` (1st of month, 06:00 UTC, queue=`billing`). **This is the only discount mechanism** — users always pay full price; refunds land afterward. Two independent passes:

**Redeemer pass** — For each `CouponRedemption`: if head paid last month (or head=None), and the redeemer paid last month, and the invoice post-dates the redemption, and no `RefundRecord` exists → issue `stripe.Refund.create` for `coupon.percent` of the redeemer's invoice.

**Head pass** — For each `Coupon` with a non-null head: if head paid last month → count redeemers who also paid → compute `percent × count` capped at 100% → issue refund on the head's invoice if no `RefundRecord` exists.

Module-level helpers: `_prev_month_window`, `_get_paid_invoice` (queries dj-stripe local `Invoice` table via `stripe_data__` JSONField lookups), `_issue_refund`, `_safe_create_refund_record` (swallows `IntegrityError` on race retry). Each coupon fails independently; `RefundRecord` constraints make the whole job idempotent on Procrastinate retry.

### `billing/admin.py`

Unregisters all dj-stripe admin models on startup (Coupon, Customer, Subscription, Invoice, Price, Product, PaymentMethod, Charge, Refund, BalanceTransaction, WebhookEventTrigger, PromotionCode, Discount) — NeverDue manages billing through its own models only.

- **`CouponAdmin`** — Primary staff interface for creating all coupons (influencer deals, staff grants, etc.). Inline `CouponRedemptionInline` shows redeemers. `code`, `percent`, `max_redemptions`, `head` become read-only after creation. `raw_id_fields` for head.
- **`CouponRedemptionAdmin`** — Read-only audit view. No add/delete permissions — rows are managed by webhook and cancellation signal.
- **`SubscriptionAdmin`** — List/search by user and Stripe IDs. `referral_coupon` read-only.
- **`RefundRecordAdmin`** — Fully read-only financial audit log.
- **`UserReferralAdmin`** — Minimal read-only User view, no tokens.

### `billing/views/` (package)

- **`pages.py`** — `plans` renders `billing/membership.html`; context includes `discount` (int %, ceiling via `compute_discount`), `active_partners` (count of active redeemers on the user's referral coupon), `referral_code`, `show_referral`. `generate_referral_code` POST view lazily creates a referral code and returns JSON. `checkout` is a GET that creates a Stripe Checkout Session with `allow_promotion_codes=True` and month-end refund notice in `custom_text`. `coupon_status` is an unauthenticated GET at `/billing/referral/<code>/` that fetches the PromotionCode live from Stripe and renders head_active, head_label, slots used/remaining.

### `billing/urls.py`

`app_name = 'billing'`. Patterns: `membership/`, `checkout/` (GET), `success/`, `cancel/`, `portal/`, `referral-code/generate/`, `referral/<str:code>/` (unauthenticated). dj-stripe webhook mounted at `/stripe/webhook/` via `project/urls.py`.

### `billing/tests/` (package)

Subdivided into `models/`, `signals/`, `tasks/`, `views/`, `integration/` — see `TESTS.md` for full plan. Each file ≤70 lines.

- **`helpers.py`** — `BillingTestCase` (tracks Stripe objects for teardown cleanup), `make_user`, `make_coupon`, `make_subscription`, `make_redemption`, `create_stripe_customer`, `create_stripe_subscription`, `make_djstripe_invoice` (seeds local djstripe Customer + Charge + Invoice rows for task tests without live Stripe calls), `sign_stripe_webhook`, `s()`.
- **`settings_test.py`** — Overrides for test run: procrastinate removed, MD5 password hasher, SQLite in-memory DB.

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
- **`queue.py`** — `queue`, `queue_status` (JSON for badge polling — returns `active_count`, `attention_count`, `jobs` array capped at 50), `queue_job_detail`, `queue_job_reprocess`, `queue_job_retry`, `queue_job_delete`, `queue_jobs_bulk_delete`.

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

Standard Django config. `AUTH_USER_MODEL = 'accounts.User'`. Procrastinate uses Postgres. Static files via WhiteNoise. Stripe keys from env. dj-stripe configured: `STRIPE_LIVE_MODE`, `DJSTRIPE_WEBHOOK_SECRET`, `DJSTRIPE_USE_NATIVE_JSONFIELD = True`, `DJSTRIPE_FOREIGN_KEY_TO_FIELD = 'id'`. Referral coupons are created per-user on demand via `Subscription.generate_referral_code()` as `Coupon` rows with `head=user` — no shared coupon in settings.

### `project/views.py`

Three passthrough views: `privacy`, `terms`, `help_page`.

### `project/staff/` (package)

- **`dashboard.py`** — `staff_dashboard` behind `@staff_required`. Aggregates ScanJob and MonthlyUsage stats, builds 3 Chart.js datasets. Defines `staff_required`, `_cost`, `_date_range`, `FAILURE_COLORS`.
- **`actions.py`** — `staff_retry_jobs`, `staff_retry_single`, `staff_delete_single`, `staff_bulk_retry`, `staff_bulk_delete`. `_parse_pks` handles JSON and form POST.

### `project/urls.py`

Staff routes under `/staff/`, app routes delegated to each app's `urls.py`. Support app mounted at `/support/`. dj-stripe webhook mounted at `/stripe/` via `include('djstripe.urls', namespace='djstripe')`.

### `project/templates/base.html`

Base layout with top nav (auth-aware links, queue badges, Pro/Upgrade indicator, Referral link, dark/light theme toggle), ad sidebar (left column, hidden on mobile ≤768px), sticky footer, and CSRF meta tag. Inline `<script>` in `<head>` reads `localStorage('nd-theme')` and sets `data-theme` on `<html>` before first paint to avoid flash. Injects `js/core/base.js` for all users (not just authenticated — needed for theme toggle).

### `project/templates/billing/membership.html`

Renders plan comparison for free users (simple `<a>` upgrade link). For pro users or users with an existing referral code, renders the referral section: discount badge, `active_partners` count, referral code display with copy button or generate button. Checkout uses `allow_promotion_codes=True` on Stripe's hosted page — codes are entered there. Also renders `coupon_status.html` partial for the unauthenticated code-check endpoint.

### `project/templates/dashboard/` (13 templates)

All extend `base.html`. `event_edit.html` supports adding/removing multiple links. `event_detail.html` displays all event links. `index.html` renders event card grid with bulk-select. `queue_job_detail.html` renders reprocess form and retry button. `rules.html` renders three rule-type forms. `categories.html` points to `billing:membership`.

### `project/templates/billing/cancel.html`

Points to `billing:membership`.

### `project/templates/accounts/login.html`

Includes a referral feature block: "Use Pro for free with referrals".

### `project/templates/help/help.html`

Includes a Referrals/Coupons section explaining how codes work, the 8-referral free tier (8 × 12.5% = 100%), and that discounts are issued as month-end refunds.

### `project/templates/support/` (3 templates)

- **`submit.html`** — Textarea + submit. LLM classifies on submission.
- **`ticket_detail.html`** — Status-aware: spinner if pending; LLM answer for `awaiting_user`; GitHub link if open/closed.
- **`my_tickets.html`** — Card list; type badge colour-coded by LLM-assigned type.

### `project/static/manual/css/base/`

- **`base.css`** — Design tokens split into `[data-theme="dark"]` (default) and `[data-theme="light"]` CSS variable blocks for full theme switching. Global resets, typography, button variants, input styles, messages, sticky footer (`position:sticky; bottom:0`), badge animations, theme toggle button styles, mobile overflow/CLS stability rules.
- **`layout.css`** — Two-column grid layout (`200px sidebar + 1fr`). Ad sidebar is `position:sticky`, `flex-direction:column` always — ads stack downward, never reposition horizontally. Hidden via `display:none` on mobile (≤768px). `.page` and `.page--wide` use `margin:auto` to center within main column. Pagination (`justify-content:center`) supports both legacy `.current` and new `.pagination__num--active` class names.
- **`forms.css`** — Form groups, labels, hints, actions row, reminder/rule rows.

### `project/static/manual/css/pages/`

Per-page stylesheets: `auth.css`, `billing.css`, `categories.css`, `dashboard.css`, `email-inbox.css`, `events.css`, `login.css`, `preferences.css`, `queue.css`, `staff.css`, `support.css`, `upload.css`. `billing.css` referral section uses `.referral-discount-badge`, `.referral-code-box`, `.referral-partners`, `.referral-empty`, `.referral-manage`.

### `project/static/manual/css/components/`

- **`house_ads.css`** — Fallback ad card styles.

### `project/static/manual/js/core/`

- **`base.js`** — Dark/light theme toggle (reads/writes `localStorage('nd-theme')`, updates `data-theme` on `<html>`). Timezone auto-detect, hamburger menu, global queue badge polling. Polls `queue_status` and reads `active_count` / `attention_count` from response. Loaded for all users (not just authenticated).
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
- **`billing.js`** — Referral code generation (POST to `generate_referral_code`, replaces button with code + copy UI) and `copyCode()` (clipboard).
- **`upload.js`** — Drag-and-drop file input enhancement.

---
