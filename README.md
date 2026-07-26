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
- Both Stripe and Adyen adapters call their REST APIs directly over plain
  HTTP (via Python's `requests`) - no Stripe SDK, matching how Odoo's own
  payment_stripe/payment_adyen modules work.

## Deliberate gaps

- No authentication or permission classes anywhere - every endpoint is open.
- Credential fields (Stripe/Adyen API keys) are stored as plain text, not
  encrypted.

Both are called out with comments at the point they matter (`settings.py`,
and on each credential field) and will be addressed in a later phase.

## Project layout

- `payhub/` - Django project settings, root URLconf
- `tenants/` - `Tenant` and `Customer` reference models
- `payments_core/` - `PaymentProvider`, `PaymentToken`, `PaymentTransaction`
  models (including the full `_process()` pipeline), the
  `PaymentProviderAdapter` interface, the `/providers/`, `/payments/`, and
  `/webhooks/...` APIs (serializers, views, urls)
- `payments_stripe/` - Stripe adapter config/token models,
  `services.get_feature_support_fields()`, `services.StripeAdapter`
  (creates real Stripe Checkout Sessions, verifies webhook signatures,
  applies status updates)
- `payments_adyen/` - Adyen adapter config/token models,
  `services.get_feature_support_fields()`, `services.AdyenAdapter` (calls
  Adyen's `/payments` and `/payments/details` endpoints directly, verifies
  per-notification-item HMAC signatures, applies status updates)

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
      "hmac_key": "hk_123"
    }
  }'
```

`code` must be `"stripe"` or `"adyen"`; the matching `*_config` object is
required; registering the same `code` twice for the same tenant returns a
400, not a raw database error.

### List a tenant's providers

`GET /providers/?tenant_id=<tenant-uuid>` - `tenant_id` is required. Response
never includes credential fields.

```bash
curl "http://localhost:8000/providers/?tenant_id=<tenant-uuid>"
```

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
6. **Known limitation:** this phase's `/payments` request doesn't send a
   `paymentMethod` (real card/iDEAL/etc. details) - Adyen's real sandbox
   API normally needs one to decide whether a payment completes
   immediately or needs a redirect, so a real request with valid
   credentials but no `paymentMethod` will likely get rejected by Adyen
   with a "required field missing" style error (which surfaces cleanly as
   `state="error"`, same as any other adapter failure - the failure path
   itself works correctly). Building a real `paymentMethod` payload is part
   of the "online_direct" flow, which is out of scope until a later phase.
   Both response shapes (immediate and redirect) are fully implemented in
   `AdyenAdapter.get_specific_processing_values` - they were verified by
   mocking `send_provider_api_request`'s return value in a Django shell
   session rather than against a real card, precisely because of this
   limitation.

## API reference (Phase 2 additions)

### Create a payment request

`POST /payments/` - creates a `PaymentTransaction` in `state="draft"`, calls
the provider's real API, then updates the transaction based on what came
back. There are two possible outcomes, and the response shape tells you
which one happened:

**1. Redirect required** (always for Stripe; Adyen when the payment method
needs 3D Secure or a similar extra step) - the transaction moves to
`state="pending"` and the response includes a `redirect_url`:

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

**2. Processed immediately** (Adyen only, for payment methods that don't
need a redirect - Adyen's `/payments` response can include a final
`resultCode` right away). No redirect exists, so `redirect_url` is `null`,
and the transaction has ALREADY been moved to its real final state
(`"done"` or `"error"`) by the time you get the response - there's nothing
further to redirect the customer to or wait on:

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
response with neither a redirect action nor a resultCode, ...), the
response is `502` and the transaction is left in `state="error"` with the
provider's error message in `state_message` - it is NOT deleted, so you can
still `GET` it afterward to see what went wrong.

### Check a payment's status

`GET /payments/{id}/` - the polling endpoint. For a redirect-based payment,
poll this after redirecting the customer and after the webhook/return-URL
flow (see below) has had a chance to run. For an Adyen payment that
completed immediately, this will already show the transaction's final
`"done"`/`"error"` state right after `POST /payments/` returns.

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
`tenant_id` in the request - the tenant/provider is either embedded in the
URL (`provider_id`) or looked up via the transaction's own `reference`.

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

`POST /webhooks/stripe/<provider_id>/` - the URL you configure in Stripe
(or point the Stripe CLI at - see "Webhook setup" below). Verifies the
`Stripe-Signature` header against that provider's `stripe_webhook_secret`
before processing anything.

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

`POST /webhooks/adyen/<provider_id>/` - the URL you configure in Adyen's
Customer Area. Verifies each notification item's own
`additionalData.hmacSignature` against that provider's `adyen_hmac_key`
before processing it. Always responds `"[accepted]"`, per Adyen's own
requirement - even notification items with a bad signature or an
unrecognized event code are just skipped (and logged), not rejected with
an error status, so Adyen doesn't endlessly retry the whole batch.

## Webhook setup

Both webhook endpoints need SOMETHING to actually call them - either a real
tool forwarding real provider events, or a hand-crafted request. Here's
both.

### Stripe CLI (recommended - drives real events)

1. Install the Stripe CLI: https://docs.stripe.com/stripe-cli
2. Log in: `stripe login` (this walks you through pairing the CLI with
   your Stripe account in a browser).
3. Forward events to your local server, for a specific provider:

   ```bash
   stripe listen --forward-to localhost:8000/webhooks/stripe/<provider-uuid>/
   ```

   The CLI prints a webhook signing secret (`whsec_...`) when it starts -
   **use that value** for the provider's `stripe_config.webhook_secret`
   (`PATCH /providers/<provider-uuid>/`), not the one from the Stripe
   Dashboard - the CLI generates its own secret for local forwarding.
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
   `http://<your-public-url>/webhooks/adyen/<provider-uuid>/` (Adyen needs
   a publicly reachable URL - use a tunnel tool like `ngrok` for local
   testing: `ngrok http 8000`, then use the `https://....ngrok.io` URL it
   gives you).
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
# 1. Create a tenant and customer directly via Django shell (no API for
#    this yet - Tenant/Customer creation isn't part of any phase's scope).
python manage.py shell -c "
from tenants.models import Tenant, Customer
t = Tenant.objects.create(name='Acme Inc')
c = Customer.objects.create(tenant=t, reference='acme-customer-1')
print('TENANT_ID=' + str(t.id))
print('CUSTOMER_ID=' + str(c.id))
"

# 2. Onboard a Stripe provider (use your own test-mode keys - see "Getting
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

# 3. Enable it (providers start "disabled" - see PaymentProvider.state).
curl -X PATCH http://localhost:8000/providers/<PROVIDER_ID>/ \
  -H "Content-Type: application/json" -d '{"state": "test"}'

# 4. Start forwarding Stripe webhooks to this provider (see "Webhook
#    setup" above) - keep this running in its own terminal:
stripe listen --forward-to localhost:8000/webhooks/stripe/<PROVIDER_ID>/

# 5. Create a payment.
curl -X POST http://localhost:8000/payments/ \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<TENANT_ID>", "provider_id": "<PROVIDER_ID>",
    "customer_id": "<CUSTOMER_ID>", "amount": "25.00", "currency": "USD",
    "return_url": "https://example.com/return"
  }'
# -> note "id" (transaction id) and "redirect_url"

# 6. Open redirect_url in a browser, pay with Stripe's test card
#    4242 4242 4242 4242 (any future expiry, any CVC). Stripe sends a
#    webhook, which the `stripe listen` terminal forwards to your local
#    server automatically.

# 7. Confirm the transaction reached "done".
curl http://localhost:8000/payments/<TRANSACTION_ID>/
# {"...", "state": "done", ...}
```

The same flow works for Adyen, substituting `code: "adyen"` /
`adyen_config` in step 2 and the Adyen webhook setup in step 4 - though see
the "known limitation" note above about Adyen needing real `paymentMethod`
details this project doesn't send yet, which makes a fully real end-to-end
Adyen run harder to trigger than Stripe's without building the
"online_direct" flow first.
