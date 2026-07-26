# nushadlabs-payhub-core

A Django/DRF learning project that reproduces the architecture of Odoo's
`payment` module (`payment.provider`, `payment.token`, `payment.transaction`)
as a standalone, multi-tenant Django REST API.

This is a portfolio/learning project, built in phases. **Phase 0** (this
state) is scaffolding only: project structure, data models, and a health
check endpoint - no payment logic yet.

## Deliberate gaps (Phase 0)

- No authentication or permission classes anywhere - every endpoint is open.
- Credential fields (Stripe/Adyen API keys) are stored as plain text, not
  encrypted.

Both are called out with comments at the point they matter (`settings.py`,
and on each credential field) and will be addressed in a later phase.

## Project layout

- `payhub/` - Django project settings, root URLconf
- `tenants/` - `Tenant` and `Customer` reference models
- `payments_core/` - `PaymentProvider`, `PaymentToken`, `PaymentTransaction`
  models, the `PaymentProviderAdapter` interface, and (in later phases) the
  core payment processing pipeline
- `payments_stripe/` - Stripe adapter config/token models (stubbed)
- `payments_adyen/` - Adyen adapter config/token models (stubbed)

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
