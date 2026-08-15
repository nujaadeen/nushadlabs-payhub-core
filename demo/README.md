# PayHub Demo Page

A single static HTML file (`demo.html`) that exercises the payhub-core API from a real browser,
for manual end-to-end testing. It is **not** served by Django - it's a completely separate static
asset, calling the API purely via `fetch()` from JavaScript running in your browser. No Django
views, templates, or URLs were added or changed to support it.

## Running it

1. Make sure the Django dev server is running as usual, from the project root:

   ```bash
   python manage.py runserver
   ```

   This demo expects the API at `http://localhost:8000` - see the `API_BASE` constant at the top
   of the `<script>` in `demo.html` if you need to point it somewhere else.

2. In a **separate** terminal, serve this `demo/` folder with Python's built-in static file
   server:

   ```bash
   cd demo
   python -m http.server 8080
   ```

3. Open `http://localhost:8080/demo.html` in your browser.

Both servers need to be running at the same time - Django for the API, the `http.server` just to
hand your browser the static HTML/JS file (you could also open `demo.html` directly as a
`file://` URL, but a real `http://` origin behaves more predictably for the redirect flow in
steps 5/6, so `http.server` is the recommended way to run this).

## What it does

Walks through the full payment cycle by calling the real API endpoints in order, all on one page
(nothing is hidden between steps - re-run any step at any time to try different scenarios):

1. Create a tenant - `POST /tenants/` - or pick an existing one from the "Or select an existing
   tenant" dropdown (`GET /tenants/`). Either path sets the same "active tenant" used by every
   later step.
2. Create a customer under that tenant - `POST /customers/` - or pick an existing one from a
   dropdown (`GET /customers/?tenant_id=...`).
3. Onboard a Stripe or Adyen provider for that tenant - `POST /providers/` - or pick an existing
   one to reuse from a dropdown (`GET /providers/?tenant_id=...`, no currency filter - lists
   everything the endpoint returns for the tenant) - then activate it (switch it out of its
   default `"disabled"` state) - `PATCH /providers/{id}/`. Note: `GET /providers/` has always
   filtered to `"enabled"`/`"test"` providers only (a pre-existing, intentional backend design,
   not something new here - see `ProviderListCreateView.get()` in `payments_core/views.py`), so a
   provider you just onboarded won't show up in this dropdown again until AFTER it's been
   activated - the create-then-activate flow still works fine in one sitting since the id is
   tracked directly, this only affects re-selecting a still-disabled provider on a later page
   load.
4. List providers filtered by currency - `GET /providers/?tenant_id=...&currency=...` - pick one
   via radio button
5. Create a payment transaction - explicit top-down selection: confirm the active tenant, pick a
   customer and a provider from dropdowns (the provider dropdown is currency-filtered, same query
   as step 4, refetched whenever the currency field changes), then amount/currency/return_url -
   `POST /payments/` - and, if a `redirect_url` comes back, follow it to Stripe's/Adyen's hosted
   checkout page
6. After being redirected back, check the final payment status - `GET /payments/{id}/`
7. Transaction history - `GET /payments/`, optionally filtered by tenant/customer/provider
   dropdowns - renders a table of every transaction, newest first. See "Steps 2/3/5: dropdowns
   and the tenant cascade" and "Step 7: transaction history" below for more detail on both of
   these additions.

Every step shows the raw JSON response from the API in a `<pre>` box (Step 7 also renders a table
above its own response box, built from that same response).

IDs from earlier steps (tenant, customer, provider, transaction) are saved in your browser's
`localStorage`, not just JS variables - they need to survive the full page navigation that
happens in steps 5/6 when your browser is sent off to Stripe's/Adyen's own page and back (see the
comment on `STORAGE_KEY` in `demo.html`). A "Reset all saved IDs" button near the top clears this
if you want to start over from a clean slate - this also clears the active tenant/customer/
provider selection and every dropdown that depends on it.

## Steps 2/3/5: dropdowns and the tenant cascade

Steps 1-5 now all revolve around one "active tenant", shown at the top of Steps 2/3/4/5 as
"Selected tenant: `<name>` (`<id>`)". It's set by EITHER creating a new tenant in Step 1 OR
picking one from that step's dropdown - both paths save the exact same `tenantId`, so everything
downstream behaves identically regardless of which one you used.

Steps 2 and 3 each add a dropdown ("Or select an existing customer/provider for this tenant") as
an alternative to their create form, and their WHOLE step (both the dropdown and the create form)
is disabled with a "Select or create a tenant in Step 1 first" note until a tenant is active -
there's no valid tenant to create a customer/provider under otherwise.

**The cascade**: whenever the active tenant changes (picking a different one from Step 1's
dropdown, or creating a brand new tenant), the currently-selected customer and provider are
cleared, and Step 2/3/5's tenant-scoped dropdowns are all refetched for the NEW tenant. Without
this, e.g. Step 5 could end up trying to create a payment mixing one tenant's customer with a
completely different tenant's provider, left over from before you switched tenants.

Step 5 restructures its selection into an explicit top-down order: confirm the tenant, pick a
customer (dropdown, same list as Step 2's), pick a provider (dropdown, filtered by whatever
currency is currently in Step 5's own currency field - refetched every time that field changes),
then amount/currency/return_url. This is a SEPARATE provider dropdown from Step 3's (which lists
every provider for the tenant with no currency filter) and from Step 4's radio buttons (which
also filter by currency, driven by Step 4's own form) - all three ultimately just set the same
`providerId`, so picking from any of them works equally well for Step 5's payment.

## Step 7: transaction history

A read-only table below Step 6, backed entirely by `GET /payments/`. It's a plain polling/refresh
pattern - a button click, a page load, a successful payment creation, or a status check all just
re-fetch the current list and redraw the table from scratch; there's no websocket or
Server-Sent-Events connection keeping it "live". That's the right amount of complexity for a
manual testing tool where a human decides when to look, not a real production dashboard.

The three filter dropdowns above the table (tenant/customer/provider) default to whichever
tenant/customer/provider is currently active elsewhere on the page, but are independent from that
point on - changing them only affects Step 7's own query, and doesn't touch the rest of the page.
Clearing the tenant filter shows transactions across every tenant; the customer and provider
filters are disabled until a tenant filter is chosen, because `GET /customers/` and
`GET /providers/` both require a `tenant_id` - there's no backend endpoint for "every customer/
provider across every tenant" (only "every transaction" has that, via unfiltered `GET
/payments/`).

The table's Tenant and Customer columns show a human-readable name/reference where possible, not
just a raw id - `GET /payments/` doesn't include those directly (it does already nest each
transaction's provider as `{id, code, name}`, which is why the Provider column doesn't need this),
so `demo.html` reuses whatever tenant/customer data it's ALREADY fetched for its own dropdowns
(via `GET /tenants/`, always the full list, and `GET /customers/?tenant_id=...`, accumulated
across every tenant browsed this session) and falls back to the bare id for anything it hasn't
seen yet - e.g. a transaction belonging to a tenant whose customers haven't been loaded in this
browser session.

## A note on CORS

`demo.html` (served from e.g. `http://localhost:8080` or `http://127.0.0.1:5500`) and the Django
API (`http://localhost:8000`) are **different origins** as far as the browser is concerned, even
though they're all "localhost"/"127.0.0.1" - the port is part of the origin too. Without extra
setup, the browser blocks this page's requests before Django ever sees them, which shows up as
`HTTP network error` in the result box (and a much more detailed CORS error in the browser's dev
console) - `Failed to fetch` on Step 1 was exactly this.

The project already handles the common cases: `django-cors-headers` is installed, and
`CORS_ALLOWED_ORIGINS` (see `.env.example` / `settings.py`) allows both
`http://127.0.0.1:5500`/`http://localhost:5500` (VS Code's "Live Server" extension) and
`http://127.0.0.1:8080`/`http://localhost:8080` (`python -m http.server`, as used above) by
default - so serving this page on either of those should just work with no further setup.

**If you serve `demo.html` from a different port than those**, you'll hit the CORS block again.
Fix it by adding your port to `CORS_ALLOWED_ORIGINS` in your own `.env` file (comma-separated, no
spaces - see `.env.example` at the project root for the exact format) and restarting the Django
dev server (Django only reads `.env` at startup, so a running server won't pick up the change on
its own). The "Calling API at" label near the top of this page shows both origins involved, which
is exactly the information you need to debug this if it comes up.
