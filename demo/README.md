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

1. Create a tenant - `POST /tenants/`
2. Create a customer - `POST /customers/`
3. Onboard a Stripe or Adyen provider - `POST /providers/` - then activate it (switch it out of
   its default `"disabled"` state) - `PATCH /providers/{id}/`
4. List providers filtered by currency - `GET /providers/?tenant_id=...&currency=...` - pick one
   via radio button
5. Create a payment transaction - `POST /payments/` - and, if a `redirect_url` comes back, follow
   it to Stripe's/Adyen's hosted checkout page
6. After being redirected back, check the final payment status - `GET /payments/{id}/`

Every step shows the raw JSON response from the API in a `<pre>` box.

IDs from earlier steps (tenant, customer, provider, transaction) are saved in your browser's
`localStorage`, not just JS variables - they need to survive the full page navigation that
happens in steps 5/6 when your browser is sent off to Stripe's/Adyen's own page and back (see the
comment on `STORAGE_KEY` in `demo.html`). A "Reset all saved IDs" button near the top clears this
if you want to start over from a clean slate.

## A note on CORS

`demo.html` (served from `http://localhost:8080`) and the Django API (`http://localhost:8000`)
are **different origins** as far as the browser is concerned, even though both say "localhost" -
the port is part of the origin too. If your browser blocks the requests, you'll see it clearly:
the result box for that step will show `HTTP network error` with a message from the browser (and
usually a much more detailed CORS error in the browser's dev console).

If that happens, the fix is to install
[`django-cors-headers`](https://pypi.org/project/django-cors-headers/) and allow
`http://localhost:8080` as an allowed origin. This is **deliberately not set up already** - this
demo page is a late, purely-additive addition to the project, and adding a CORS-handling
dependency and settings change on the Django side isn't done unless it turns out to actually be
necessary for your browser/setup.
