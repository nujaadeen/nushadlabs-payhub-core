# nushadlabs-payhub-core

A Django/DRF learning project that reproduces the architecture of Odoo's
`payment` module (`payment.provider`, `payment.token`, `payment.transaction`)
as a standalone, multi-tenant Django REST API.

This is a portfolio/learning project, built in phases.

- **Phase 0**: scaffolding only - project structure, data models, a health
  check endpoint. No payment logic.
- **Phase 1**: onboarding - a tenant can register and manage
  `PaymentProvider` records (Stripe or Adyen config).
- **Phase 2**: payment request creation - a customer can be handed a real,
  working Stripe Checkout Session or Adyen redirect via `POST /payments/`,
  then poll `GET /payments/{id}/` for status. Only the "online_redirect"
  flow is implemented - no embedded card fields, no charging a saved
  token.
- **Phase 3** (this state): webhooks + redirect completion - the
  asynchronous half of the cycle. A `"pending"` transaction now actually
  reaches a terminal state (`"done"`, `"cancel"`, or `"error"`), driven by
  either Stripe's/Adyen's webhook calling us, or the customer's browser
  returning from the provider's page and us following up with the
  provider directly. See "Webhook setup" and "Full end-to-end walkthrough"
  below. **No duplicate-delivery guard yet** - both providers can deliver
  the same webhook event more than once (that's expected, normal behavior
  on their end, e.g. after a retry), and this phase re-processes it every
  time rather than recognizing "I've already handled this one" (see the
  `# TODO: Phase 5` comments in `StripeWebhookView`/`AdyenWebhookView`).
  In practice this doesn't corrupt anything - a duplicate delivery for an
  already-`"done"` transaction hits `_update_state`'s allowed-source-states
  guard (see `payments_core/models.py`), which refuses the repeat
  transition; the webhook views' broad `except Exception` catches that and
  logs it, then still responds 200/`"[accepted]"` so the provider doesn't
  keep retrying - so it's noisy in the logs rather than harmful, but it's
  still a gap worth knowing about.
- **Currency filtering** (this state): `GET /providers/?tenant_id=...`
  accepts an optional `&currency=` filter so a tenant's payment page can
  list only the providers that support a customer's chosen currency. This
  mirrors Odoo's `payment.provider._get_supported_currencies()` exactly:
  which currencies a provider supports is a **hardcoded constant in the
  adapter's own code** (`payments_stripe/const.py`,
  `payments_adyen/const.py`), NOT a database field - a tenant can't
  configure or override it via the API. See "List a tenant's providers"
  below.
- Both Stripe and Adyen adapters call their REST APIs directly over plain
  HTTP (via Python's `requests`) - no Stripe SDK, matching how Odoo's own
  payment_stripe/payment_adyen modules work.

## Deliberate gaps

- No authentication or permission classes anywhere - every endpoint is open.
- Credential fields (Stripe/Adyen API keys) are stored as plain text, not
  encrypted.
- `django.contrib.admin` (and `auth`/`sessions`/`messages`, which it needs)
  is **not installed** - see "No admin app" below.

These are called out with comments at the point they matter (`settings.py`,
and on each credential field) and will be addressed in a later phase.

## No admin app

This project has never used Django's admin site, so `django.contrib.admin`
- along with `django.contrib.auth`, `django.contrib.sessions`, and
`django.contrib.messages`, which admin/auth need - has been removed from
`INSTALLED_APPS` entirely (see the comments there in `settings.py`). Those
apps would otherwise create tables this project never reads from
(`auth_user`, `django_session`, `django_admin_log`, ...) on every
`migrate`. A fresh `migrate` now only creates tables for our own apps, plus
`django_content_type` and `django_migrations` (Django/DRF's own
unavoidable internals - see the `INSTALLED_APPS` comment on
`django.contrib.contenttypes` in `settings.py` for exactly why
`contenttypes` still has to stay even though nothing in this project uses
it directly).

**If you need to inspect data during development**, use
`python manage.py shell` or connect directly with a Postgres client (e.g.
`psql`) - not the admin app.

**If your local database predates this change** (i.e. you ran `migrate`
before admin/auth/sessions/messages were removed), it will still have the
old `auth_*`/`django_session`/`django_admin_log`/`django_content_type`
tables sitting around unused. The simplest fix, since this is local
development data: drop and recreate the database, then run `migrate`
fresh, rather than fighting Django's migration history for apps that no
longer exist:

```bash
dropdb <your-db-name>
createdb <your-db-name> -O <your-db-user>
python manage.py migrate
```

(or the equivalent `DROP DATABASE` / `CREATE DATABASE` SQL, if you set the
database up directly through `psql` rather than `dropdb`/`createdb`).

## Project layout

- `payhub/` - Django project settings, root URLconf
- `tenants/` - `Tenant` and `Customer` models, plus the `/tenants/` and
  `/customers/` creation-only endpoints (serializers, views, urls)
- `payments_core/` - `PaymentProvider`, `PaymentToken`, `PaymentTransaction`
  models (including the full `_process()` pipeline), the
  `PaymentProviderAdapter` interface, the `/providers/`, `/payments/`, and
  `/webhooks/...` APIs (serializers, views, urls)
- `payments_stripe/` - Stripe adapter config/token models,
  `services.get_feature_support_fields()`, `services.StripeAdapter`
  (creates real Stripe Checkout Sessions, verifies webhook signatures,
  applies status updates)
- `payments_adyen/` - Adyen adapter config/token models,
  `services.get_feature_support_fields()`, `services.AdyenAdapter` (creates
  Adyen Sessions API payments in Hosted Checkout mode - see "Adyen: Sessions
  API + Hosted Checkout" below - and verifies per-notification-item HMAC
  signatures, applies status updates)

## Setup

### 1. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example env file and fill in real values (at minimum, your local
Postgres credentials):

```bash
cp .env.example .env
```

`CORS_ALLOWED_ORIGINS` in that file already covers the common ways to
serve `demo/demo.html` locally (VS Code "Live Server" on port 5500,
`python -m http.server` on port 8080) - see `demo/README.md` if you're
serving it from somewhere else.

### 4. Create the Postgres database

Make sure a Postgres server is running and a database/user matching your
`.env` exists, e.g.:

```bash
createuser payhub -P
createdb payhub -O payhub
```

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Run the dev server

```bash
python manage.py runserver
```

### 7. Verify

```bash
curl http://localhost:8000/health/
# {"status": "ok"}
```

## API reference (tenants app)

No auth, matching every other endpoint. List and create only - no
get-one/update/delete for either of these yet; that's out of scope so far.

### List tenants

`GET /tenants/` - returns every tenant. No filtering (there's no "tenant of
a tenant" concept anywhere in this project - every tenant is top-level) and
no pagination (small demo/admin tool, not expecting thousands of tenants).

```bash
curl http://localhost:8000/tenants/
```

### Create a tenant

```bash
curl -X POST http://localhost:8000/tenants/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Inc"}'
```

### List a tenant's customers

`GET /customers/?tenant_id=<tenant-uuid>` - `tenant_id` is required (mirrors
`GET /providers/?tenant_id=...` below) - a missing `tenant_id` returns a
clean 400 rather than silently returning every customer across every
tenant.

```bash
curl "http://localhost:8000/customers/?tenant_id=<tenant-uuid>"
```

### Create a customer

`tenant` must be a real tenant UUID (a made-up or missing tenant returns a
clean 400, not a 500 - DRF validates the foreign key automatically). A
customer's `reference` only has to be unique per-tenant, not globally -
registering the same `reference` twice under the same tenant returns a
clean 400.

```bash
curl -X POST http://localhost:8000/customers/ \
  -H "Content-Type: application/json" \
  -d '{"tenant": "<tenant-uuid>", "reference": "acme-customer-1"}'
```

## API reference (Phase 1)

There is no auth on any of these - every endpoint is open. All examples
assume the dev server is running on `localhost:8000`.

### Create a provider

`POST /providers/` - creates a `PaymentProvider` row plus its matching
credential row (`PaymentProviderStripeConfig` or `PaymentProviderAdyenConfig`)
in one request. `state` always starts as `"disabled"`.

Stripe:

```bash
curl -X POST http://localhost:8000/providers/ \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<tenant-uuid>",
    "code": "stripe",
    "name": "My Stripe Account",
    "stripe_config": {
      "publishable_key": "pk_test_123",
      "secret_key": "sk_test_123",
      "webhook_secret": "whsec_123"
    }
  }'
```

Adyen:

```bash
curl -X POST http://localhost:8000/providers/ \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<tenant-uuid>",
    "code": "adyen",
    "name": "My Adyen Account",
    "adyen_config": {
      "merchant_account": "MA123",
      "api_key": "ak_123",
      "client_key": "ck_123",
      "hmac_key": "hk_123",
      "theme_id": "your-adyen-theme-id"
    }
  }'
```

`theme_id` is optional at this step (a provider can exist without one), but
`POST /payments/` will fail with a clear error for this provider until it's
set - see "Adyen: Sessions API + Hosted Checkout" below for what it is and
where to get one.

`code` must be `"stripe"` or `"adyen"`; the matching `*_config` object is
required; registering the same `code` twice for the same tenant returns a
400, not a raw database error.

### List a tenant's providers

`GET /providers/?tenant_id=<tenant-uuid>` - `tenant_id` is required. Only
returns providers with `state` `"enabled"` or `"test"` (a `"disabled"`
provider is still reachable directly via `GET /providers/<uuid>/`, just not
listed here - mirrors Odoo's own "don't show a switched-off provider to a
customer" filtering). Response never includes credential fields.

```bash
curl "http://localhost:8000/providers/?tenant_id=<tenant-uuid>"
```

Add `&currency=<code>` to only get back providers that support a given
currency (case-insensitive - `?currency=usd` and `?currency=USD` behave the
same):

```bash
curl "http://localhost:8000/providers/?tenant_id=<tenant-uuid>&currency=USD"
```

**Important: this is NOT backed by a database field.** Which currencies a
provider supports is a hardcoded constant living in that provider's own
adapter code - `payments_stripe/const.py` and `payments_adyen/const.py`
each export a `SUPPORTED_CURRENCIES` list, and
`StripeAdapter`/`AdyenAdapter.get_supported_currencies()` (in each app's
`services.py`) just return it. This mirrors Odoo's actual
`payment.provider._get_supported_currencies()`, which is a *method*
individual provider modules override in code, not a field a merchant
configures per record - there's no equivalent input on `POST /providers/`
or `PATCH /providers/{id}/`, and there never will be one; every tenant
using `"stripe"` gets the exact same supported-currency list as every other
tenant using `"stripe"`.

**To add support for a currency**, edit the relevant adapter's `const.py`
directly (e.g. add `"CHF"` to `payments_stripe/const.py`'s
`SUPPORTED_CURRENCIES` list) - that's the one place this needs to change,
and it takes effect for every tenant using that provider immediately (no
migration, no per-tenant update needed).

Filtering logic: a provider matches a `currency` filter if its adapter's
`get_supported_currencies()` returns `None` (the base/default behavior -
"no restriction, supports every currency") or a list that contains the
given code. Since there's no database column to filter on, this can't be
done with a SQL `WHERE` clause - `ProviderListCreateView.get()` fetches the
tenant's (state-filtered) providers first, then checks each one's adapter
in plain Python. Note: `POST /payments/` does NOT currently re-check
currency compatibility itself (see the `# TODO` comment near
`PaymentCreateView` in `payments_core/views.py`) - it relies on only ever
being called with a provider a caller already picked from this
currency-filtered list.

### Get one provider

```bash
curl http://localhost:8000/providers/<provider-uuid>/
```

### Update a provider

`PATCH /providers/<provider-uuid>/` - partial update. Can update
`name`, `state`, `minimum_amount`, `maximum_amount`, `is_published`,
`allow_tokenization`, `capture_manually`, `allow_express_checkout`, and the
matching nested config (also partial - only send the credential fields
you're rotating).

```bash
curl -X PATCH http://localhost:8000/providers/<provider-uuid>/ \
  -H "Content-Type: application/json" \
  -d '{
    "state": "test",
    "is_published": true,
    "stripe_config": {"webhook_secret": "whsec_rotated"}
  }'
```

### Delete a provider

`DELETE /providers/<provider-uuid>/` - soft delete only: sets `state` to
`"disabled"` and `is_published` to `false`. The row is never actually
removed (see the `on_delete=PROTECT` comments on `PaymentToken.provider` /
`PaymentTransaction.provider` in `payments_core/models.py`).

```bash
curl -X DELETE http://localhost:8000/providers/<provider-uuid>/
# 204 No Content
```

## Getting test-mode API keys (Phase 2)

`POST /payments/` makes a REAL call to Stripe's or Adyen's TEST/sandbox API
- you need real (test-mode) credentials from each provider to exercise it
end-to-end. Invalid credentials still work for demonstrating the failure
path (the transaction ends up in `state="error"` with the provider's own
error message) - see below.

**Stripe:**

1. Create a free Stripe account at https://dashboard.stripe.com/register
2. Make sure you're in **Test mode** (toggle in the top-right of the dashboard).
3. Go to **Developers -> API keys** and copy the **Publishable key**
   (`pk_test_...`) and **Secret key** (`sk_test_...`).
4. For `stripe_config.webhook_secret`, see "Webhook setup" below - it now
   matters, since the webhook endpoint verifies requests against it.

**Adyen:**

1. Request a free Adyen test account at https://www.adyen.com/signup (Adyen
   provisions test accounts manually - this can take a bit).
2. In the Customer Area, go to **Developers -> API credentials**, create a
   set of API credentials, and copy the **API key**.
3. Your **Merchant account** name is shown in the Customer Area header /
   account switcher.
4. The `client_key` is under the same API credentials page (used for
   client-side integrations - not actually used by anything in this phase's
   server-to-server call, but the field exists for a future phase).
5. This project always calls Adyen's TEST endpoint
   (`checkout-test.adyen.com`), hardcoded in `payments_adyen/services.py` -
   never a production URL.
6. `theme_id`: see "Adyen: Sessions API + Hosted Checkout" right below -
   you'll need to create one in the Adyen Customer Area before a real
   payment against this provider will succeed.

### Adyen: Sessions API + Hosted Checkout

An earlier phase called Adyen's raw `/payments` endpoint directly, mirroring
Odoo's `payment_adyen` module. That turned out to be the wrong endpoint for
this project and has been replaced - here's why, and what changed.

**Why `/payments` didn't work for us:** `/payments` requires a
`paymentMethod` object describing exactly how the shopper wants to pay
(card details, iDEAL bank, etc.) - real requests without one are rejected
with `"Required object 'paymentMethod' is not provided"`. Odoo's own
`payment_adyen` module only avoids this because Odoo ships a frontend
(Adyen's Drop-in/Components JS) that collects those details in the
browser, before the server ever calls `/payments`. This project is
backend-only and redirect-first by design - there's no frontend collecting
payment method details, and no request-body change fixes this, because
`/payments` architecturally assumes a frontend that we deliberately don't
have.

**The fix - Adyen's Sessions API, in `"hosted"` mode:**
`AdyenAdapter.get_specific_processing_values` (`payments_adyen/services.py`)
now calls `POST /sessions` on Adyen's Checkout API with `"mode": "hosted"`.
This is Adyen's equivalent of Stripe Checkout Sessions above: Adyen hosts
the entire payment page itself, and the `/sessions` response includes a
ready-made `url` to redirect the shopper to - we never collect or see any
payment method details ourselves, matching how the Stripe adapter already
works. This is a deliberate difference from Odoo's `payment_adyen` module,
not a partial/incomplete port of it - Odoo doesn't support Hosted Checkout
mode at all (it assumes Drop-in), so there's nothing to mirror there.

Hosted Checkout has two real prerequisites, confirmed against Adyen's
current integration docs while building this (not guessed):

- **API v72 or later** for the `/sessions` call specifically (the
  `"mode": "hosted"` request field and the `url` response field aren't
  available on v71, which is why `payments_adyen/services.py` pins two
  different Adyen API versions for two different endpoints - see the
  comments on `ADYEN_API_VERSION` and `ADYEN_SESSIONS_API_VERSION` there).
- **A `theme_id`** - Adyen requires a "theme" (page branding/layout) to
  exist before Hosted Checkout will work. There's no API to create one;
  it's a one-time manual step per Adyen merchant account: in the Adyen
  Customer Area, go to **Pay by Link -> Themes**, create a theme, and copy
  its id into `adyen_config.theme_id` (see "Create a provider" above).
  `PaymentProviderAdyenConfig.adyen_theme_id` is nullable - a provider can
  exist without one - but `POST /payments/` for that provider will fail
  immediately with a clear `state="error"` message telling you exactly
  this, rather than the confusing old `paymentMethod` error.

One behavioral consequence: because Hosted Checkout always redirects the
shopper to Adyen's own page, Adyen payments in this project now always
follow the "redirect required" outcome below - the old "processed
immediately, no redirect" outcome was only ever reachable via raw
`/payments`, and no longer happens for Adyen.

`search_by_reference`/`apply_updates` in `AdyenAdapter` needed no changes
for this - Adyen's async webhook notification shape
(`notificationItems`/`eventCode`/`merchantReference`/`pspReference`) is the
same regardless of which API created the payment, and both methods were
already written against that payload shape rather than against a specific
call site.

**Known follow-up (not yet fixed):** `AdyenPaymentsDetailsView`/
`AdyenReturnView`/`_complete_adyen_payments_details` (`payments_core/views.py`)
still implement the old Drop-in-style continuation (`POST
/payments/details` with a `details` body) for the return trip after a
redirect. Hosted Checkout's actual continuation is different - Adyen
redirects the shopper back to `return_url` with `sessionId`/`sessionResult`
appended, and the correct next call is `GET /sessions/{id}?sessionResult=...`,
not `POST /payments/details`. In practice this doesn't block payments from
resolving correctly end-to-end, since Adyen's webhook (`AdyenWebhookView`)
independently reports the same `AUTHORISATION` result and drives the
transaction to its final state regardless - but the return-URL continuation
path itself is a known mismatch worth fixing in a later phase.

## API reference (Phase 2 additions)

### Create a payment request

`POST /payments/` - creates a `PaymentTransaction` in `state="draft"`, calls
the provider's real API, then updates the transaction based on what came
back. There are two possible outcomes, and the response shape tells you
which one happened:

**1. Redirect required** (always, for both Stripe and Adyen - see "Adyen:
Sessions API + Hosted Checkout" above for why Adyen always redirects too,
now) - the transaction moves to `state="pending"` and the response includes
a `redirect_url`:

```json
{
  "id": "<transaction-uuid>",
  "reference": "tx-1234567890",
  "state": "pending",
  "redirect_url": "https://checkout.stripe.com/c/pay/cs_test_..."
}
```

Send your end-customer's browser to `redirect_url` to let them complete the
payment on Stripe's/Adyen's hosted page. As of Phase 3, the transaction
moves on from `"pending"` to a terminal state via either the provider's
webhook calling us, or the customer's browser returning to your own
`return_url` and your frontend calling our return-URL endpoint - see
"Webhook setup" and "Full end-to-end walkthrough" below. Poll
`GET /payments/{id}/` to see the result either way.

**2. Processed immediately** (historical - not currently reachable by
either adapter). This outcome existed when Adyen's raw `/payments` endpoint
could return a final `resultCode` right away for some payment methods; now
that Adyen uses Sessions/Hosted Checkout (see above), Adyen always
redirects too, the same as Stripe. The response shape and the
`_apply_updates`-then-report code path in `PaymentCreateView` (`payments_core/views.py`)
are left in place rather than deleted, since nothing about them is wrong -
an adapter CAN still report `redirect_url: null` this way if a future
adapter or flow ever produces an immediate result again:

```json
{
  "id": "<transaction-uuid>",
  "reference": "tx-1234567891",
  "state": "done",
  "redirect_url": null
}
```

Either way: `provider_id` must belong to `tenant_id`; the provider must not
be `state="disabled"`; `amount` must be within the provider's
`minimum_amount`/`maximum_amount` if either is set. If the call to
Stripe/Adyen itself fails (bad credentials, network error, an Adyen
`/sessions` response missing `url`, ...), the response is `502` and the
transaction is left in `state="error"` with the provider's error message in
`state_message` - it is NOT deleted, so you can still `GET` it afterward to
see what went wrong. For an Adyen provider with no `theme_id` configured
yet (see "Adyen: Sessions API + Hosted Checkout" above), this fails the
same way, with a message telling you exactly that.

### List transactions

`GET /payments/` - returns transactions, newest first (ordered by
`created_at` descending - mirrors Odoo's own `payment.transaction._order =
"id desc"`; we order by `created_at` instead of `id` since our primary key
is a UUID, which - unlike Odoo's auto-incrementing integer id - has no
ordering meaning of its own). All three query params below are optional
and combine as an AND filter; omit all of them to get every transaction
across every tenant (this is our own admin/debug view with no auth, so
that's intentional here, unlike `GET /customers/` above which requires
`tenant_id`). No pagination yet:

```bash
curl http://localhost:8000/payments/
curl "http://localhost:8000/payments/?tenant_id=<tenant-uuid>"
curl "http://localhost:8000/payments/?tenant_id=<tenant-uuid>&customer_id=<customer-uuid>"
curl "http://localhost:8000/payments/?provider_id=<provider-uuid>"
```

Each transaction includes a small nested `provider` object (`id`, `code`,
`name`) alongside the plain `provider_id`, so a transaction-history UI can
show e.g. "Stripe - Acme Stripe" without a separate `GET /providers/{id}/`
call per row:

```json
[
  {
    "id": "<transaction-uuid>",
    "tenant_id": "<tenant-uuid>",
    "customer_id": "<customer-uuid>",
    "provider_id": "<provider-uuid>",
    "provider": {"id": "<provider-uuid>", "code": "stripe", "name": "Acme Stripe"},
    "reference": "tx-1234567890",
    "amount": "25.00",
    "currency": "USD",
    "state": "done",
    "state_message": null,
    "provider_reference": "cs_test_...",
    "created_at": "...",
    "updated_at": "..."
  }
]
```

### Check a payment's status

`GET /payments/{id}/` - the polling endpoint. Poll this after redirecting
the customer and after the webhook/return-URL flow (see below) has had a
chance to run. Deliberately a smaller field list than the list endpoint
above - just enough for a caller to know what's going on with their one
payment, not the tenant/customer/provider detail a history view needs.

```bash
curl http://localhost:8000/payments/<transaction-uuid>/
```

```json
{
  "id": "<transaction-uuid>",
  "reference": "tx-1234567890",
  "state": "pending",
  "amount": "25.00",
  "currency": "USD",
  "provider_reference": "cs_test_...",
  "state_message": null,
  "created_at": "...",
  "updated_at": "..."
}
```

## API reference (Phase 3 additions)

Five new endpoints, all under `/webhooks/...`. None of these need
`tenant_id` in the request - the tenant/provider is always looked up via
the transaction's own `reference` (see "Webhook URLs are fixed" below for
why nothing needs to be embedded in the URL either).

### Stripe return URL

`GET /webhooks/stripe/return/?reference=<reference>` - call this (e.g. from
your own frontend) once the customer lands back on your `return_url` after
Stripe's checkout page. Fetches the live Checkout Session from Stripe and
processes it.

```bash
curl "http://localhost:8000/webhooks/stripe/return/?reference=tx-1234567890"
# {"reference": "tx-1234567890", "state": "done"}
```

### Stripe webhook

`POST /webhooks/stripe/` - one FIXED URL (no provider id or any other
identifier in the path) - configure this same URL in Stripe regardless of
how many tenants/providers you have (or point the Stripe CLI at it - see
"Webhook setup" below). Finds the matching transaction via
`PaymentTransaction._search_by_reference` (using the event payload's own
`client_reference_id`) FIRST, then verifies the `Stripe-Signature` header
against THAT transaction's own provider's `stripe_webhook_secret` - see
"Webhook URLs are fixed" below for why this works with no per-tenant URL.
An event whose reference doesn't match any transaction is acknowledged
with a plain `200`, not a `404` or `500` - there's nothing to do with it,
and Stripe shouldn't be told to retry something that will never resolve.

### Adyen return URL

`GET /webhooks/adyen/return/?merchantReference=<reference>&redirectResult=<result>` -
call this once the customer lands back after a 3D Secure challenge.

### Adyen payments/details

`POST /webhooks/adyen/payments-details/` - the 3DS "continuation" call. A
real frontend would call this with whatever `details` Adyen's client-side
SDK produced after the shopper completed a challenge.

```bash
curl -X POST http://localhost:8000/webhooks/adyen/payments-details/ \
  -H "Content-Type: application/json" \
  -d '{"reference": "tx-1234567890", "details": {"redirectResult": "..."}}'
# {"reference": "tx-1234567890", "state": "done"}
```

### Adyen webhook

`POST /webhooks/adyen/` - one FIXED URL, same rationale as the Stripe
webhook above - configure this same URL in Adyen's Customer Area
regardless of how many tenants/providers you have. For each notification
item, finds the matching transaction via `_search_by_reference` (using
that item's own `merchantReference`) FIRST, then verifies that item's own
`additionalData.hmacSignature` against THAT transaction's provider's
`adyen_hmac_key`. Always responds `"[accepted]"`, per Adyen's own
requirement - items with no matching transaction, a bad signature, or an
unrecognized event code are just skipped (and logged), not rejected with
an error status, so Adyen doesn't endlessly retry the whole batch.

## Webhook URLs are fixed

`/webhooks/stripe/` and `/webhooks/adyen/` are single, fixed URLs - the
same URL works for every tenant and every provider, with no id of any kind
in the path. This matches Odoo's own webhook URLs exactly
(`/payment/stripe/webhook`, `/payment/adyen/notification` are also fixed,
single paths in Odoo).

An earlier phase of this project put `provider_id` in these URLs instead
(e.g. `/webhooks/stripe/<provider_id>/`) as a workaround for having no
auth - the idea being "the URL tells us which tenant's secret to check
against". That turned out to be unnecessary, and didn't match Odoo: Odoo's
webhook handlers don't need to know the provider up front either, because
the webhook PAYLOAD itself already carries the info needed to find it.
Stripe's `client_reference_id` and Adyen's `merchantReference` are OUR OWN
transaction reference (set when we created the Checkout Session / Sessions
call), and `PaymentTransaction._search_by_reference` can already find the
matching transaction from that alone - unchanged from Phase 3, since this
method always worked correctly, it just wasn't being used for THIS
purpose yet. Once the transaction is found, its `provider` foreign key
gets you the right provider - and the right webhook secret / HMAC key to
verify against - with nothing needed from the URL at all. See
`StripeWebhookView`/`AdyenWebhookView` in `payments_core/views.py` for the
full code-level explanation.

One consequence worth calling out: because the URL no longer identifies a
provider, an unrecognized/unmatched reference is now acknowledged with a
plain `200`, not a `404` - there's genuinely nothing to distinguish "this
reference doesn't exist" from "this reference belongs to a different,
perfectly valid provider we just don't need to specially reject" (Stripe/
Adyen would otherwise interpret a non-2xx as "please retry", which makes
no sense for a reference that will never resolve). This mirrors Odoo's own
`if not tx_sudo: return request.make_json_response("")` behavior exactly.

## Webhook setup

Both webhook endpoints need SOMETHING to actually call them - either a real
tool forwarding real provider events, or a hand-crafted request. Here's
both.

### Stripe CLI (recommended - drives real events)

1. Install the Stripe CLI: https://docs.stripe.com/stripe-cli
2. Log in: `stripe login` (this walks you through pairing the CLI with
   your Stripe account in a browser).
3. Forward events to your local server - one fixed URL, not tied to any
   particular provider (see "Webhook URLs are fixed" above):

   ```bash
   stripe listen --forward-to localhost:8000/webhooks/stripe/
   ```

   The CLI prints a webhook signing secret (`whsec_...`) when it starts -
   **use that value** for the provider's `stripe_config.webhook_secret`
   (`PATCH /providers/<provider-uuid>/`), not the one from the Stripe
   Dashboard - the CLI generates its own secret for local forwarding. Note
   this is specific to `stripe listen`: it forwards every event from your
   one logged-in Stripe test account through one local secret, so if
   you're testing more than one provider row locally at once, they'd all
   need that same CLI-generated secret. A real deployment would give each
   tenant's own Stripe account its own webhook endpoint and its own
   distinct secret, same as always - only the URL you point them at is
   shared now, not the secret.
4. In another terminal, create a payment (`POST /payments/`) against that
   same provider, then either:
   - Open the returned `redirect_url` in a browser and pay with a
     [Stripe test card](https://docs.stripe.com/testing) (e.g.
     `4242 4242 4242 4242`, any future expiry, any CVC), or
   - Trigger a test event directly: `stripe trigger checkout.session.completed`
     (note: this fires a generic test event, not necessarily tied to your
     specific transaction's `client_reference_id` - opening the real
     `redirect_url` and paying is the more realistic end-to-end test).
5. Watch the `stripe listen` terminal - it shows each event being
   forwarded and the HTTP status our webhook view returned.

### Adyen test webhook tool

1. In your Adyen Customer Area, go to **Developers -> Webhooks**, and add
   a "Standard notification" webhook pointing at
   `http://<your-public-url>/webhooks/adyen/` - one fixed URL, not tied to
   any particular provider (see "Webhook URLs are fixed" above). Adyen
   needs a publicly reachable URL - use a tunnel tool like `ngrok` for
   local testing: `ngrok http 8000`, then use the `https://....ngrok.io`
   URL it gives you.
2. Set the webhook's **HMAC Key** - this is the SAME value you store in
   that provider's `adyen_config.hmac_key` (`PATCH /providers/<provider-uuid>/`).
   Generate one via Adyen's UI if you haven't already (the "Generate new
   HMAC key" button on the webhook's settings page).
3. Adyen's Customer Area has a **"Test configuration"** button on each
   webhook - use it to send a real, correctly-signed test notification to
   your endpoint without needing an actual payment. Adjust the test
   payload's `merchantReference` to match a real transaction's `reference`
   if you want it to actually find and update one.
4. Alternatively, without any Adyen account at all: this project's
   `AdyenAdapter.apply_updates`/`search_by_reference`/HMAC verification
   were verified directly by constructing a signed `notificationItems`
   payload in Python (matching Adyen's documented HMAC algorithm exactly -
   see `_compute_adyen_hmac_signature` in `payments_adyen/services.py`)
   and POSTing it with `curl` - see that function's docstring for the
   exact algorithm if you want to build your own test payload by hand.

## Full end-to-end walkthrough

Putting every phase together - onboard a provider, create a payment, drive
it to completion via a webhook, and confirm the final state:

```bash
# 1. Create a tenant.
curl -X POST http://localhost:8000/tenants/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Inc"}'
# -> note the returned "id" (TENANT_ID)

# 2. Create a customer under that tenant.
curl -X POST http://localhost:8000/customers/ \
  -H "Content-Type: application/json" \
  -d '{"tenant": "<TENANT_ID>", "reference": "acme-customer-1"}'
# -> note the returned "id" (CUSTOMER_ID)

# 3. Onboard a Stripe provider (use your own test-mode keys - see "Getting
#    test-mode API keys" above).
curl -X POST http://localhost:8000/providers/ \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<TENANT_ID>", "code": "stripe", "name": "Acme Stripe",
    "stripe_config": {
      "publishable_key": "pk_test_...", "secret_key": "sk_test_...",
      "webhook_secret": "whsec_..."
    }
  }'
# -> note the returned provider "id"

# 4. Enable it (providers start "disabled" - see PaymentProvider.state).
curl -X PATCH http://localhost:8000/providers/<PROVIDER_ID>/ \
  -H "Content-Type: application/json" -d '{"state": "test"}'

# 5. Start forwarding Stripe webhooks - one fixed URL, works for every
#    provider (see "Webhook URLs are fixed" above) - keep this running in
#    its own terminal:
stripe listen --forward-to localhost:8000/webhooks/stripe/

# 6. Create a payment.
curl -X POST http://localhost:8000/payments/ \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<TENANT_ID>", "provider_id": "<PROVIDER_ID>",
    "customer_id": "<CUSTOMER_ID>", "amount": "25.00", "currency": "USD",
    "return_url": "https://example.com/return"
  }'
# -> note "id" (transaction id) and "redirect_url"

# 7. Open redirect_url in a browser, pay with Stripe's test card
#    4242 4242 4242 4242 (any future expiry, any CVC). Stripe sends a
#    webhook, which the `stripe listen` terminal forwards to your local
#    server automatically.

# 8. Confirm the transaction reached "done".
curl http://localhost:8000/payments/<TRANSACTION_ID>/
# {"...", "state": "done", ...}
```

The same flow works for Adyen, substituting `code: "adyen"` / `adyen_config`
(including a real `theme_id` - see "Adyen: Sessions API + Hosted Checkout"
above) in step 3 and the Adyen webhook setup in step 5. As of that fix,
Adyen's redirect-based flow works the same way Stripe's does above - open
`redirect_url` in a browser, complete the payment on Adyen's hosted page,
and the webhook (or return-URL call - see the "known follow-up" note above
about that path's current continuation-endpoint mismatch) drives the
transaction to `"done"`.

## Idempotency & Hardening

This phase didn't add any new business capability, endpoint, or change any
existing request/response shape - it solidified behavior that was already
supposed to exist, closing a few real gaps in how safely this project
handles being called more than once, and tightened naming fidelity to
Odoo. No model fields changed either - `is_post_processed` already existed
from Phase 0, so `python manage.py migrate` has nothing new to apply.

### Webhook idempotency - no event-dedup table

Stripe and Adyen both retry webhook deliveries that don't get a fast
enough 2xx response, and can just send the same event/notification twice
regardless of timing. This project does **not** handle that with a
separate "have I already processed this event?" table - neither does
Odoo. Idempotency instead falls directly out of
`PaymentTransaction._update_state` (`payments_core/models.py`), which
every `_set_pending`/`_set_done`/`_set_error`/etc. call goes through:

```python
if self.state == target_state:
    logger.info("Skipped the update of transaction %s as it is already in state %s.", ...)
    return self
if self.state not in allowed_states:
    logger.warning("Refused to update transaction %s from state %s to state %s; ...", ...)
    return self
# ... an actual transition happens here, and is_post_processed is reset to False
```

A second webhook for an already-`"done"` transaction hits the first
check, gets logged at INFO (this is normal, expected traffic - not an
error), and returns without touching the row again. A transition to a
state the transaction can't currently reach from hits the second check
and is logged at WARNING, also without raising - both guards `return
self` rather than raising an exception, a deliberate change from an
earlier version of this method that used to `raise ValueError` for the
second case (see the method's own docstring in `models.py` for the full
reasoning: a webhook caller has no useful way to "fix" an unexpected
transition and retry anyway).

**Unmatched references** (a webhook whose reference doesn't match any
transaction) are a separate, already-existing safeguard from Phase 3.7,
confirmed still correct here: both webhook views acknowledge with a plain
`200` and do nothing, rather than 404ing or erroring - see "Webhook URLs
are fixed" above.

**`_post_process` / `is_post_processed`**: mirrors Odoo's
`payment.transaction._post_process()` - called at the end of `_process()`,
after `_apply_updates`/`_validate_amount`, and simply flags
`is_post_processed = True`. `_update_state` resets it back to `False` on
every ACTUAL state transition (not on the skip/refuse cases above), so a
transaction that changes state more than once (e.g. `done` → `error` via
a later amount-mismatch check) gets flagged for post-processing again
rather than staying marked from its first pass. No cron/Celery job
processes stuck-`pending` transactions yet - see the
`# TODO: consider a periodic reconciliation job...` comment next to
`_post_process` (mirrors Odoo's own `_cron_post_process`) for why that's
explicitly out of scope rather than silently missing.

### Reference collision handling

`PaymentTransaction._compute_reference` reads existing references and
computes the next suffix in Python, then `POST /payments/`'s view does a
separate `create()` call afterward - a small race window exists if two
requests for the *same tenant* land in that window at nearly the same
instant. Mirroring Odoo's own approach: the database's `(tenant,
reference)` unique constraint (`PaymentTransaction.Meta`) is the actual
source of truth, not anything preventing the race in application code.
`PaymentListCreateView.post()` catches the resulting `IntegrityError` and
retries the whole compute-then-create step once; losing that retry too
(vanishingly unlikely) returns a `409 Conflict`. No `select_for_update()`
or similar locking - payment creation isn't a high-enough-frequency hot
path here to justify it.

### Sensitive-field log masking

`payments_core/logging_utils.py` adds `mask_sensitive(data, sensitive_keys)`
(returns a masked shallow copy, never mutates the original) and
`get_masked_logger(name, sensitive_keys=...)` (a completely standard
Python logger, with `sensitive_keys` attached as a convenience attribute).
This is a deliberately simplified, **explicitly-called** version of
Odoo's `get_payment_logger` - Odoo's real version hooks into Python's
logging system globally so every log call gets masked automatically; this
project's version only masks where a call site actually calls
`mask_sensitive()` first. Each provider's real credential field names
live in its own `SENSITIVE_KEYS` constant (`payments_stripe/const.py`,
`payments_adyen/const.py`) - applied to the outbound request/response
logging in `send_provider_api_request` (`payments_core/utils.py`,
including the `Authorization`/`X-API-Key` headers that actually carry
each provider's secret) and to incoming webhook payload logging in both
webhook views.

### Naming fidelity to Odoo

Every adapter interface method (`payments_core/interfaces.py`,
`payments_stripe/services.py`, `payments_adyen/services.py`) is now
prefixed with a leading underscore, matching Odoo's real method names on
`payment.provider`/`payment.transaction` exactly (e.g. Odoo's actual
method is `_get_specific_processing_values`, not
`get_specific_processing_values`). This is a naming convention only -
Python has no enforced privacy the way some frameworks do, and neither
does Odoo's own underscore convention; it's there so a reader who already
knows Odoo's payment framework can match a method here to its Odoo
equivalent by name alone. `_search_by_reference` exists at BOTH layers
(the `PaymentTransaction` classmethod, unchanged from Phase 3, and each
adapter's own instance method, renamed this phase) - this isn't a
collision, it mirrors Odoo's own two-layer structure where the model
method dispatches to a provider-specific override of the same name.
Webhook signature verification (`_verify_webhook_signature`) stays on the
adapter here rather than moving to the view/controller layer the way
Odoo splits it (Stripe's `_verify_signature` and Adyen's
`_compute_signature` both live on Odoo's controllers, not its models) -
a deliberate, reasonable structural difference, not a fidelity gap; see
`PaymentProviderAdapter._verify_webhook_signature`'s own docstring for
the full reasoning.
