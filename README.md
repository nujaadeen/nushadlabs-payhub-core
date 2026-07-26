# nushadlabs-payhub-core

A Django/DRF learning project that reproduces the architecture of Odoo's
`payment` module (`payment.provider`, `payment.token`, `payment.transaction`)
as a standalone, multi-tenant Django REST API.

This is a portfolio/learning project, built in phases.

- **Phase 0**: scaffolding only - project structure, data models, a health
  check endpoint. No payment logic.
- **Phase 1**: onboarding - a tenant can register and manage
  `PaymentProvider` records (Stripe or Adyen config).
- **Phase 2** (this state): payment request creation - a customer can be
  handed a real, working Stripe Checkout Session or Adyen redirect via
  `POST /payments/`, then poll `GET /payments/{id}/` for status. Only the
  "online_redirect" flow is implemented - no embedded card fields, no
  charging a saved token. **No webhook handling yet** (that's Phase 3), so
  a transaction that ends up `"pending"` (waiting on the customer to finish
  on Stripe's/Adyen's page) will sit there forever - nothing exists yet to
  move it on to `"done"` from a webhook. The one exception: Adyen can
  sometimes complete a payment immediately, with no redirect and no
  webhook needed at all - see "Create a payment request" below.
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
  models, the `PaymentProviderAdapter` interface, the `/providers/` API
  (serializers, views, urls), and (in later phases) the core payment
  processing pipeline
- `payments_stripe/` - Stripe adapter config/token models,
  `services.get_feature_support_fields()`, `services.StripeAdapter`
  (creates real Stripe Checkout Sessions)
- `payments_adyen/` - Adyen adapter config/token models,
  `services.get_feature_support_fields()`, `services.AdyenAdapter` (calls
  Adyen's `/payments` endpoint directly)

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
4. A webhook secret isn't needed until Phase 3 (webhooks) - any placeholder
   string works for `stripe_config.webhook_secret` for now.

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
payment on Stripe's/Adyen's hosted page. Poll `GET /payments/{id}/`
afterward - though as noted above, nothing moves this past `"pending"` until
Phase 3's webhook handling exists.

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
poll this after redirecting the customer - though nothing in this phase
moves a `"pending"` transaction any further yet (that's Phase 3, webhooks).
For an Adyen payment that completed immediately, this will already show the
transaction's final `"done"`/`"error"` state right after `POST /payments/`
returns.

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
