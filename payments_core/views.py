import json
import logging

from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from payments_adyen.services import (
    ADYEN_TEST_PAYMENTS_DETAILS_URL,
    AdyenAdapter,
    adyen_event_to_result_code,
)
from payments_stripe.const import HANDLED_WEBHOOK_EVENTS as STRIPE_HANDLED_WEBHOOK_EVENTS
from payments_stripe.services import (
    STRIPE_API_VERSION,
    STRIPE_CHECKOUT_SESSIONS_URL,
    StripeAdapter,
)
from tenants.models import Customer

from .exceptions import PaymentProviderRequestError
from .models import PaymentProvider, PaymentTransaction, get_adapter_for_provider_code
from .serializers import (
    PaymentProviderCreateSerializer,
    PaymentProviderReadSerializer,
    PaymentProviderUpdateSerializer,
    PaymentTransactionCreateSerializer,
    PaymentTransactionReadSerializer,
)
from .utils import send_provider_api_request

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """
    A DRF APIView (as opposed to a plain Django view) gets us DRF's request
    parsing, content negotiation, and browsable-API rendering "for free" -
    even though this view is trivial, using APIView here matches how every
    real endpoint we add in later phases will be built.

    Because REST_FRAMEWORK's DEFAULT_PERMISSION_CLASSES is AllowAny (see
    settings.py), no auth/permission check runs before `get()` below - any
    caller, authenticated or not, can hit this endpoint.
    """

    def get(self, request):
        return Response({"status": "ok"})


class ProviderListCreateView(APIView):
    """Handles GET /providers/ and POST /providers/."""

    def get(self, request):
        # We have no auth, so there's no logged-in tenant to scope this
        # query to automatically (Odoo would infer this from the current
        # user's company). Instead we require the caller to tell us
        # explicitly which tenant's providers they want, via ?tenant_id=.
        tenant_id = request.query_params.get("tenant_id")
        if not tenant_id:
            return Response(
                {"tenant_id": "This query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # QuerySets are lazy - .filter() below doesn't hit the database yet,
        # it just builds up a SQL query description. The database is only
        # actually queried once we do something that needs the results,
        # which happens below when we iterate over `providers` to apply
        # the currency filter (or, if there's no currency param, once the
        # serializer iterates over it to build the response list).
        #
        # state__in=["enabled", "test"]: mirrors Odoo's own
        # _get_compatible_providers filtering - a "disabled" provider is one
        # a tenant configured but switched off, and shouldn't show up on a
        # customer-facing payment page at all (it's still visible/editable
        # via GET/PATCH /providers/{id}/ directly by id, just not listed
        # here alongside the providers a customer could actually pay with).
        providers = PaymentProvider.objects.filter(
            tenant_id=tenant_id, state__in=["enabled", "test"]
        )

        currency = request.query_params.get("currency")
        if currency:
            currency = currency.upper()
            # Which currencies a provider supports is NOT a database field
            # on PaymentProvider - it's a hardcoded constant living in each
            # adapter's own code (see payments_stripe/const.py,
            # payments_adyen/const.py), read via
            # adapter.get_supported_currencies() - exactly mirroring Odoo's
            # payment.provider._get_supported_currencies(), which is a
            # method individual provider modules override, not something a
            # merchant configures per record. That means we CAN'T filter
            # this with a SQL WHERE clause the way state__in above does -
            # there's nothing in the payment_core_paymentprovider TABLE to
            # query against. Instead, we fetch the tenant's (already
            # state-filtered) providers first, then - in plain Python - ask
            # EACH one's adapter what currencies it supports, and keep only
            # the matching ones. This is strictly less efficient than a
            # DB-level filter (it can't be pushed down into the query, and
            # wouldn't scale to thousands of providers) but is the right
            # trade-off here: a tenant realistically has a handful of
            # providers, and this mirrors Odoo's own architecture, where
            # currency support is also just Python code that runs after
            # providers are loaded, not a SQL-level filter.
            matching_providers = []
            for provider in providers:
                adapter = get_adapter_for_provider_code(provider.code)
                supported_currencies = adapter.get_supported_currencies()
                if supported_currencies is None:
                    # None means "no restriction" (see
                    # PaymentProviderAdapter.get_supported_currencies in
                    # interfaces.py) - include this provider regardless of
                    # which currency was requested.
                    matching_providers.append(provider)
                elif currency in (code.upper() for code in supported_currencies):
                    matching_providers.append(provider)
            providers = matching_providers

        serializer = PaymentProviderReadSerializer(providers, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = PaymentProviderCreateSerializer(data=request.data)
        # raise_exception=True: if validation fails, DRF raises a
        # ValidationError internally, which DRF's exception handler catches
        # and turns into a 400 response with the validation errors as the
        # body - we never see that response construction here, it happens
        # for us.
        serializer.is_valid(raise_exception=True)
        try:
            provider = serializer.save()
        except IntegrityError:
            # This catches the database-level (tenant, code) uniqueness
            # constraint (see PaymentProvider.Meta.unique_together). We
            # don't pre-check this in the serializer because that check
            # would have a race condition: two requests for the same
            # tenant+code could both pass a "does this already exist?" check
            # at the same instant, then both try to insert. The database
            # constraint is the only thing that can catch that reliably.
            # Without this except block, that failure would surface to the
            # API caller as a raw 500 Internal Server Error with a Postgres
            # error message, instead of a clean, understandable 400.
            return Response(
                {"code": "A provider with this code already exists for this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            PaymentProviderReadSerializer(provider).data, status=status.HTTP_201_CREATED
        )


class ProviderDetailView(APIView):
    """Handles GET/PATCH/DELETE on /providers/{id}/."""

    def get_object(self, pk):
        # get_object_or_404 is a Django shortcut: it runs
        # PaymentProvider.objects.get(pk=pk) and, if that raises
        # PaymentProvider.DoesNotExist, converts it into an Http404
        # exception instead. DRF's exception handler catches Http404 the
        # same way it catches ValidationError, and turns it into a clean
        # 404 response - so we never have to write that error-handling
        # ourselves.
        return get_object_or_404(PaymentProvider, pk=pk)

    def get(self, request, pk):
        provider = self.get_object(pk)
        return Response(PaymentProviderReadSerializer(provider).data)

    def patch(self, request, pk):
        provider = self.get_object(pk)
        # partial=True tells DRF "treat every field on this serializer as
        # optional for this call" - without it, a ModelSerializer normally
        # requires every non-nullable field to be present, which would make
        # PATCH behave like a full PUT replace instead of a true partial
        # update.
        serializer = PaymentProviderUpdateSerializer(provider, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        provider = serializer.save()
        return Response(PaymentProviderReadSerializer(provider).data)

    def delete(self, request, pk):
        provider = self.get_object(pk)
        # Soft delete, not a real DELETE: mirrors Odoo's
        # _unlink_except_master_data, which blocks hard-deleting a provider
        # outright. We flip it to disabled/unpublished instead. This also
        # matches the on_delete=PROTECT we set on PaymentToken.provider and
        # PaymentTransaction.provider (payments_core/models.py) - the
        # database itself would refuse a hard delete of a provider that
        # still has tokens or transactions pointing at it, so a real DELETE
        # here would fail for any provider that's actually been used.
        provider.state = "disabled"
        provider.is_published = False
        provider.save(update_fields=["state", "is_published", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PaymentCreateView(APIView):
    """
    Handles POST /payments/ - mirrors Odoo's
    PaymentPortal._create_transaction() -> payment.transaction.create() ->
    tx._get_processing_values() -> adapter._send_payment_request() flow,
    simplified to the "online_redirect" operation only.
    """

    def post(self, request):
        serializer = PaymentTransactionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        provider = get_object_or_404(PaymentProvider, pk=data["provider_id"])

        if provider.tenant_id != data["tenant_id"]:
            # We have no auth, so nothing stops a caller from passing a
            # provider_id that belongs to a DIFFERENT tenant than the
            # tenant_id they also passed - this check is our only guard
            # against that mix-up (e.g. tenant A accidentally, or
            # maliciously, creating a payment against tenant B's Stripe
            # account).
            return Response(
                {"provider_id": "This provider does not belong to the given tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Named _ensure_provider_is_not_disabled to match Odoo's method
            # of the same name exactly (see payments_core/models.py).
            provider._ensure_provider_is_not_disabled()
        except ValueError as exc:
            return Response({"provider_id": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Mirrors the min/max amount filtering Odoo's _get_compatible_providers
        # does when picking which providers a customer COULD use for a given
        # amount - simplified here to a straight comparison, since we're
        # validating the ONE provider the caller already chose, not
        # auto-selecting from several candidates.
        if provider.minimum_amount is not None and data["amount"] < provider.minimum_amount:
            return Response(
                {"amount": f"Amount must be at least {provider.minimum_amount}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provider.maximum_amount is not None and data["amount"] > provider.maximum_amount:
            return Response(
                {"amount": f"Amount must be at most {provider.maximum_amount}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # TODO: consider validating currency compatibility at POST
        # /payments/ time too (against provider.get_supported_currencies()
        # - see ProviderListCreateView.get's currency filter, which uses
        # the same adapter method). Not implemented here yet: a real
        # frontend would only ever show a customer providers that already
        # support their chosen currency (via that GET
        # /providers/?currency= filter), so this endpoint doesn't currently
        # double-check it too.

        customer = get_object_or_404(
            Customer, pk=data["customer_id"], tenant_id=data["tenant_id"]
        )

        reference = PaymentTransaction._compute_reference(
            provider_code=provider.code, tenant_id=data["tenant_id"]
        )

        # Step 1: create the draft row and let it COMMIT before we make any
        # external call. We deliberately do NOT wrap this create() and the
        # adapter call below in a single transaction.atomic() block - that
        # would be the natural beginner instinct ("wrap the whole thing so
        # it's all-or-nothing!"), but atomic() is for grouping DATABASE
        # writes together. The adapter call a few lines down is a NETWORK
        # REQUEST to Stripe/Adyen's servers, which can take seconds, or
        # time out, or hang. Holding a database transaction (and its locks,
        # and its connection) open for however long that network call takes
        # would be pure downside with no upside - so instead we commit the
        # draft row first, make the external call as a separate step, then
        # do a second, separate save with whatever the call returned.
        txn = PaymentTransaction.objects.create(
            tenant_id=data["tenant_id"],
            provider=provider,
            customer=customer,
            reference=reference,
            currency=data["currency"].upper(),
            amount=data["amount"],
            operation="online_redirect",
            return_url=data["return_url"],
        )

        # Step 2: the external call - NOT inside a database transaction.
        try:
            processing_values = txn._get_processing_values()
        except PaymentProviderRequestError as exc:
            txn._set_error(state_message=str(exc))
            return Response(
                {
                    "id": str(txn.id),
                    "reference": txn.reference,
                    "state": txn.state,
                    "state_message": txn.state_message,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Step 3: a second, separate save recording what the adapter call
        # returned - this only runs once we know the external call actually
        # succeeded. Note this save() uses update_fields=[...], which limits
        # the UPDATE to just these three columns - it does NOT touch
        # state/state_message/last_state_change, so it's safe to run even
        # if the adapter call below already changed those (see the
        # no-redirect branch).
        txn.provider_reference = processing_values.get("provider_reference")
        txn.provider_data = processing_values.get("raw_response")
        txn.save(update_fields=["provider_reference", "provider_data", "updated_at"])

        redirect_url = processing_values.get("redirect_url")
        if redirect_url:
            # The normal redirect-based flow (Stripe always; Adyen when it
            # needs the shopper to complete a 3DS/redirect step): the
            # customer still has to go finish the payment on the provider's
            # page, so this transaction is "pending" until that happens (or
            # until a Phase 3 webhook tells us otherwise).
            txn._set_pending()
        # else: this is Adyen's immediate-processing case (see
        # AdyenAdapter.get_specific_processing_values in
        # payments_adyen/services.py) - the adapter has ALREADY called
        # transaction._apply_updates() and moved this transaction to its
        # real final state (e.g. "done" or "error") before returning here.
        # Calling _set_pending() now would be wrong - and would actually
        # raise ValueError, since "done"/"error" aren't in _set_pending's
        # allowed source states (see _update_state) - so we skip it
        # entirely and just report back whatever state the transaction is
        # already in.

        return Response(
            {
                "id": str(txn.id),
                "reference": txn.reference,
                "state": txn.state,
                "redirect_url": redirect_url,
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentDetailView(APIView):
    """
    Handles GET /payments/{id}/ - the polling endpoint an API caller uses to
    check a payment's current status (mirrors Odoo's `/payment/status` page
    polling pattern, but as a plain JSON endpoint since we have no UI).

    As of this phase, a "pending" transaction no longer sits there forever:
    the webhook views and return-URL views below (StripeWebhookView,
    StripeReturnView, AdyenWebhookView, AdyenReturnView,
    AdyenPaymentsDetailsView) all eventually call
    PaymentTransaction._process(), which is what actually drives a
    transaction on to "done"/"authorized"/"cancel"/"error". This view
    itself doesn't do any of that work - it just reads whatever state the
    transaction is CURRENTLY in.
    """

    def get(self, request, pk):
        txn = get_object_or_404(PaymentTransaction, pk=pk)
        return Response(PaymentTransactionReadSerializer(txn).data)


class StripeReturnView(APIView):
    """
    Handles GET /webhooks/stripe/return/ - mirrors Odoo's `stripe_return`
    controller.

    Design note: this is NOT the URL Stripe redirects the customer's
    browser to - that's still `transaction.return_url` (the TENANT's own
    page, set by whoever called POST /payments/, unchanged from Phase 2).
    Odoo's `stripe_return` IS the literal browser redirect target, and
    renders a real webpage there; we're a pure JSON API with nothing to
    render, so we split that into two separate things: the customer's
    browser lands on the tenant's OWN page (as configured), and THIS
    endpoint is what that page is expected to call afterward (e.g. from
    its own backend, or client-side JS) - passing along the `reference`
    Stripe appended to that page's URL (see the `?reference=` Phase 2's
    StripeAdapter adds to success_url/cancel_url) - so we can go check with
    Stripe directly what actually happened and move the transaction on.
    """

    def get(self, request):
        reference = request.query_params.get("reference")
        if not reference:
            return Response(
                {"error": "reference query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tx = PaymentTransaction._search_by_reference("stripe", {"reference": reference})
        if tx is None:
            return Response(
                {"error": "No matching transaction found."}, status=status.HTTP_404_NOT_FOUND
            )

        config = tx.provider.stripe_config
        try:
            session = send_provider_api_request(
                "GET",
                f"{STRIPE_CHECKOUT_SESSIONS_URL}/{tx.provider_reference}",
                headers={
                    "Authorization": f"Bearer {config.stripe_secret_key}",
                    "Stripe-Version": STRIPE_API_VERSION,
                },
            )
        except PaymentProviderRequestError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        PaymentTransaction._process(
            "stripe", {"reference": reference, "checkout_session": session}
        )

        tx.refresh_from_db()
        return Response({"reference": tx.reference, "state": tx.state})


class StripeWebhookView(APIView):
    """
    Handles POST /webhooks/stripe/<uuid:provider_id>/ - mirrors Odoo's
    Stripe webhook controller.

    # TODO: Phase 5 - guard against duplicate webhook delivery
    # re-processing the same event (Stripe retries webhooks that don't get
    # a fast 2xx response, and can also just send the same event twice -
    # see Stripe's own docs on webhook idempotency).

    Design note on the URL: Odoo uses ONE shared webhook endpoint for every
    tenant/provider, and figures out which provider a webhook belongs to
    from the event's OWN content plus its normal auth/admin layer. We have
    NO auth at all, so there's no other way to know "which tenant's
    stripe_webhook_secret should I verify this against" - embedding
    provider_id in the URL is our pragmatic stand-in for what Stripe's own
    "Connect" webhook routing would otherwise give us. It's what lets us
    look up the RIGHT provider's secret to verify the signature against,
    BEFORE we trust anything in the request body.

    Django/DRF gotcha this view works around: DRF's `request.data`
    AUTO-PARSES the request body into a dict the first time you touch it -
    convenient normally, but wrong here. Signature verification needs the
    EXACT raw bytes Stripe hashed to produce the signature; even one
    different whitespace character between what Stripe sent and what we'd
    get back from re-serializing a parsed-and-rebuilt dict would make our
    HMAC computation not match theirs. So this view deliberately reads
    `request.body` (raw bytes, straight from Django's underlying
    HttpRequest - DRF doesn't touch or alter this) and does its OWN
    `json.loads()` on it afterward, and never touches `request.data` at
    all in this view.

    Also note: we don't need an `@csrf_exempt` decorator here, even though
    Stripe's servers obviously can't supply a Django CSRF token. DRF's
    `APIView.as_view()` wraps EVERY view it builds with `csrf_exempt`
    automatically - Django's CSRF protection exists to stop a malicious
    WEBSITE from tricking a logged-in user's BROWSER into making a request
    using their session cookie; DRF re-applies that protection itself only
    for `SessionAuthentication`, which this project doesn't use anywhere
    (see REST_FRAMEWORK in settings.py) - so it's simply never in play on
    any view in this whole project, this one included.
    """

    def post(self, request, provider_id):
        provider = get_object_or_404(PaymentProvider, pk=provider_id, code="stripe")

        raw_body = request.body  # bytes - see docstring above for why
        # Django exposes incoming HTTP headers as request.META keys,
        # prefixed with "HTTP_" and with dashes turned into underscores -
        # this is how a WSGI server hands headers to Django under the hood.
        # DRF's `request.headers` (a friendlier, case-insensitive mapping)
        # reads the exact same underlying data and would work just as well
        # here; request.META is what we use simply to keep this close to
        # how Odoo's own Django-adjacent code reads headers.
        signature_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

        adapter = StripeAdapter()
        try:
            adapter.verify_webhook_signature(
                raw_body, signature_header, provider.stripe_config.stripe_webhook_secret
            )
        except PaymentProviderRequestError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        event = json.loads(raw_body)
        event_type = event.get("type")

        if event_type not in STRIPE_HANDLED_WEBHOOK_EVENTS:
            # event["data"]["object"] for this event type is NOT a Checkout
            # Session (e.g. charge.succeeded/payment_intent.succeeded carry
            # a Charge/PaymentIntent instead) - see HANDLED_WEBHOOK_EVENTS
            # in payments_stripe/const.py for the full explanation. Reading
            # client_reference_id off an object that doesn't have it would
            # just produce a "no reference" warning below, for no benefit -
            # so we skip straight to acknowledging receipt instead. This is
            # a normal, expected outcome for most events Stripe sends us,
            # not an error: Stripe fans a single checkout out into several
            # event types, and we only act on the ones we understand.
            logger.info(
                "Stripe webhook: received event type '%s' for provider %s - "
                "not in HANDLED_WEBHOOK_EVENTS, acknowledging without "
                "processing.",
                event_type,
                provider_id,
            )
            return Response({"status": "received"})

        # Safe to read client_reference_id directly here: the
        # HANDLED_WEBHOOK_EVENTS check above guarantees this object is
        # genuinely a Checkout Session for every event type in this branch,
        # so "checkout_session" below is an accurate key name, not a
        # misleading one - contrast with the old code (before this fix),
        # which used this same key for EVERY event type regardless of
        # whether the object actually was one.
        checkout_session = event.get("data", {}).get("object", {})
        payment_data = {
            "reference": checkout_session.get("client_reference_id"),
            "checkout_session": checkout_session,
        }

        try:
            PaymentTransaction._process("stripe", payment_data)
        except Exception:
            # Mirrors Odoo's own webhook controllers, which deliberately
            # catch broad exceptions here (not just our own
            # PaymentProviderRequestError) and log rather than let them
            # propagate - ANY non-2xx response makes Stripe retry the same
            # webhook again later, so a bug on OUR side would otherwise
            # turn into an endless retry loop instead of just being visible
            # in our own logs. Signature failures are the one case that
            # SHOULD be a non-2xx (403, above) - a bad signature isn't
            # going to fix itself on retry, so there's no point Stripe
            # trying again.
            logger.exception(
                "Error while processing Stripe webhook for provider %s", provider_id
            )

        return Response({"status": "received"})


def _complete_adyen_payments_details(reference, details):
    """
    Shared logic for AdyenPaymentsDetailsView and AdyenReturnView below -
    both end up doing exactly the same thing (look up the transaction, POST
    to Adyen's /payments/details with whatever `details` payload we have,
    run the result through _process()), just triggered by different
    callers - a frontend calling us directly vs. the customer's browser
    landing back on our return URL after a 3DS challenge. Factored out once
    here instead of duplicated in both views.
    """
    tx = PaymentTransaction._search_by_reference("adyen", {"merchantReference": reference})
    if tx is None:
        return Response(
            {"error": "No matching transaction found."}, status=status.HTTP_404_NOT_FOUND
        )

    config = tx.provider.adyen_config
    try:
        response_content = send_provider_api_request(
            "POST",
            ADYEN_TEST_PAYMENTS_DETAILS_URL,
            headers={"X-API-Key": config.adyen_api_key, "Content-Type": "application/json"},
            json={"details": details},
        )
    except PaymentProviderRequestError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    PaymentTransaction._process("adyen", dict(response_content, merchantReference=reference))

    tx.refresh_from_db()
    return Response({"reference": tx.reference, "state": tx.state})


class AdyenPaymentsDetailsView(APIView):
    """
    Handles POST /webhooks/adyen/payments-details/ - mirrors Odoo's
    `adyen_payment_details` controller.

    This is the "continuation" call for Adyen payment methods that need an
    extra round-trip after the initial /payments call - typically 3D
    Secure (see AdyenAdapter.get_specific_processing_values in
    payments_adyen/services.py, which returns a `redirect_url` for exactly
    this case). A real frontend integration would collect whatever
    "details" Adyen's own client-side SDK produces after the shopper
    completes that challenge and POST them here. This project has no
    frontend to actually DO that collection, so this endpoint exists for
    completeness/parity with Odoo's architecture rather than being
    something we've fully exercised end-to-end with a real 3DS challenge
    ourselves.

    Unlike the webhook views above, this is a plain JSON POST from OUR OWN
    caller (a frontend we'd build later) - not a request we need to verify
    a provider's signature on - so DRF's normal automatic `request.data`
    JSON parsing is completely fine here. There's no raw-body gotcha for
    this particular view.
    """

    def post(self, request):
        reference = request.data.get("reference")
        details = request.data.get("details")
        if not reference or details is None:
            return Response(
                {"error": "reference and details are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _complete_adyen_payments_details(reference, details)


class AdyenReturnView(APIView):
    """
    Handles GET /webhooks/adyen/return/ - mirrors Odoo's
    `adyen_return_from_3ds_auth` controller. In a real deployment this
    would be the `returnUrl` Adyen redirects the customer's browser to
    after a 3DS challenge, appending `merchantReference` and
    `redirectResult` as query params - same "no frontend of our own"
    caveat as AdyenPaymentsDetailsView above applies to how this would
    actually get exercised end-to-end.
    """

    def get(self, request):
        reference = request.query_params.get("merchantReference")
        redirect_result = request.query_params.get("redirectResult")
        if not reference or not redirect_result:
            return Response(
                {"error": "merchantReference and redirectResult are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _complete_adyen_payments_details(reference, {"redirectResult": redirect_result})


class AdyenWebhookView(APIView):
    """
    Handles POST /webhooks/adyen/<uuid:provider_id>/ - mirrors Odoo's
    `adyen_webhook` controller.

    # TODO: Phase 5 - guard against duplicate webhook delivery
    # re-processing the same event.

    Same design rationale for provider_id being in the URL as
    StripeWebhookView above - see that view's docstring. We use the
    provider looked up from the URL (fetched once, up front) to verify
    EVERY notification item in this request against, rather than looking
    up each item's own provider via _search_by_reference first - verifying
    against attacker-controllable data before we've verified anything would
    be backwards, and since Adyen delivers webhooks to a URL configured
    PER merchant account, every item arriving at THIS url should belong to
    THIS provider anyway.

    Unlike Stripe (which signs the whole request body once), Adyen signs
    EACH item inside `notificationItems` individually - so there's no
    single raw-body HMAC check for the whole request the way Stripe has.
    Because of that, this view uses `request.data` normally (DRF's
    automatic JSON parsing is completely fine here - the signature we need
    to check lives INSIDE specific fields of the already-parsed body, not
    in the exact raw bytes of the body itself, so there's no gotcha to work
    around this time).
    """

    def post(self, request, provider_id):
        provider = get_object_or_404(PaymentProvider, pk=provider_id, code="adyen")
        hmac_key = provider.adyen_config.adyen_hmac_key
        adapter = AdyenAdapter()

        for item_wrapper in request.data.get("notificationItems", []):
            item = item_wrapper.get("NotificationRequestItem", {})

            try:
                adapter.verify_webhook_signature(item, hmac_key)
            except PaymentProviderRequestError:
                # Mirrors Odoo: skip just this ONE bad item rather than
                # rejecting the whole request - Adyen batches multiple
                # notifications into one HTTP call, so one bad signature
                # (or one item that's actually meant for a different
                # merchant account) shouldn't stop us from processing the
                # rest.
                logger.warning(
                    "Adyen webhook: signature verification failed for one notification item "
                    "on provider %s.",
                    provider_id,
                )
                continue

            result_code = adyen_event_to_result_code(
                item.get("eventCode"), item.get("success") == "true"
            )
            if result_code is None:
                continue

            payment_data = dict(item, resultCode=result_code)

            try:
                PaymentTransaction._process("adyen", payment_data)
            except Exception:
                # Same reasoning as StripeWebhookView's broad except -
                # never let a processing bug turn into an endless Adyen
                # retry loop; log it and move on to the next item.
                logger.exception("Error while processing an Adyen webhook notification item.")

        # Adyen's own integration docs require this EXACT literal string in
        # the response body to acknowledge receipt - not a general "ok" or
        # "success" message. Response("[accepted]") JSON-encodes it as the
        # string "[accepted]" (with quotes), which is what Adyen expects to
        # see.
        return Response("[accepted]")
