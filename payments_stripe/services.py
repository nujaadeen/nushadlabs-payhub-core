import hashlib
import hmac
import logging
import time

from payments_core.exceptions import PaymentProviderRequestError
from payments_core.interfaces import PaymentProviderAdapter
from payments_core.utils import (
    send_provider_api_request,
    to_major_currency_units,
    to_minor_currency_units,
)

# Importing payments_core.models at the TOP of this file (rather than
# lazily inside a function, the way payments_core/models.py has to import
# US) is safe here and does NOT create a circular import: payments_core's
# only import of payments_stripe.services is deferred until a method
# actually RUNS (see _get_adapter_for_provider_code in
# payments_core/models.py), so nothing forces THIS file to be loaded while
# payments_core.models is still being defined. payments_stripe/models.py
# already imports payments_core.models at its own top level too, for the
# same reason - this isn't a new pattern.
from payments_core.models import PaymentTransaction

logger = logging.getLogger(__name__)

# Stripe versions its whole API by date and expects that date sent as the
# Stripe-Version header on every request (there's no version number in the
# URL itself, unlike Adyen). Pinning a specific version here - instead of
# leaving it unset, which would silently use whatever Stripe's CURRENT
# default is - means Stripe can't change this endpoint's response shape out
# from under us later without us noticing (a version bump would be a
# deliberate, visible change to this one line).
STRIPE_API_VERSION = "2023-10-16"
STRIPE_CHECKOUT_SESSIONS_URL = "https://api.stripe.com/v1/checkout/sessions"

# How old (in seconds) a webhook's timestamp is allowed to be before we
# reject it outright. This guards against a "replay attack" - someone
# capturing a genuine, correctly-signed webhook request and resending it
# later to trigger the same state change again. 600 seconds (10 minutes)
# mirrors Odoo's own WEBHOOK_AGE_TOLERANCE.
STRIPE_WEBHOOK_AGE_TOLERANCE = 600

# Stripe PaymentIntent status -> the PaymentTransaction state-transition
# method that status should drive. Mirrors Odoo's own status mapping
# conceptually, trimmed down to a plain dict - Odoo tracks a couple of
# PaymentIntent statuses we don't need to distinguish here (e.g. it treats
# 'requires_action' and 'processing' as meaningfully different for some UI
# purposes; we just treat both as "still pending").
STRIPE_PAYMENT_INTENT_STATUS_TRANSITIONS = {
    "succeeded": "_set_done",
    "requires_capture": "_set_authorized",
    "processing": "_set_pending",
    "requires_action": "_set_pending",
    "requires_payment_method": "_set_error",
    "canceled": "_set_canceled",
}


def get_feature_support_fields(provider):
    """
    Stripe's override of PaymentProvider._compute_feature_support_fields().

    Mirrors Odoo's pattern where the payment_stripe module overrides
    payment.provider._compute_feature_support_fields() to declare what
    Stripe actually supports. We don't have Odoo's module-level model
    inheritance in Django, so instead this is a plain function: it starts
    from the base ("nothing implemented") result and then overrides
    whichever keys THIS adapter actually has working code for.

    Right now we've only built payment creation (and that's not even wired
    up to a real Stripe API call yet - that's Phase 2). Capture, refund,
    tokenization, and express checkout are not implemented for Stripe, so
    every key below stays "not_implemented".

    # Phase 2/3 TODO: once send_capture_request / send_refund_request /
    # tokenization / express checkout are actually implemented for Stripe,
    # flip the matching key here (e.g. support_refund = "full") - this
    # function is the one place that needs to change.
    """
    support_fields = provider._compute_feature_support_fields()
    # No overrides yet - see the TODO above.
    return support_fields


class StripeAdapter(PaymentProviderAdapter):
    """
    Stripe's implementation of the PaymentProviderAdapter contract
    (payments_core/interfaces.py). PaymentProviderAdapter is an ABC with
    every method marked @abstractmethod, so Python won't even let us
    instantiate this class unless every single one of those methods is
    overridden below - that's true even for the methods we're only
    stubbing out this phase.

    Phase 2 implements the "online_redirect" flow only:
    get_specific_processing_values() (creates a real Stripe Checkout
    Session) and send_payment_request() (a thin wrapper around it). Every
    other method below is still a stub - see its docstring for which phase
    implements it for real.
    """

    def get_specific_processing_values(self, transaction):
        """
        Mirrors Odoo's payment_stripe module's `_stripe_create_intent` /
        `_stripe_prepare_payment_intent_payload`, simplified down to
        Stripe Checkout Sessions - Stripe's "hosted redirect" product,
        which is all we're implementing this phase (no embedded card
        fields, no charging a saved token).

        This calls Stripe's REST API directly over plain HTTP, via
        send_provider_api_request() (payments_core/utils.py) - NOT the
        `stripe` Python SDK. That matches Odoo's own payment_stripe module,
        which never uses Stripe's SDK either; every Stripe call in Odoo
        goes through one generic `_send_api_request` helper, which is what
        send_provider_api_request() mirrors here.
        """
        # `transaction.provider.stripe_config` reaches across the
        # OneToOneField from payments_stripe/models.py
        # (PaymentProviderStripeConfig.provider, related_name="stripe_config")
        # to get this tenant's Stripe credentials for this specific
        # provider row.
        config = transaction.provider.stripe_config

        amount_minor_units = to_minor_currency_units(transaction.amount, transaction.currency)

        # Stripe's API expects `application/x-www-form-urlencoded` bodies,
        # NOT JSON - this surprises people expecting a modern REST API to
        # take JSON, but Stripe's API predates that convention and has kept
        # form encoding for backwards compatibility ever since. Nested
        # objects/arrays (like `line_items`) are expressed with bracket
        # notation in the flat key names below (e.g.
        # "line_items[0][price_data][currency]") rather than as actual
        # nested Python dicts/lists - `requests` sends a flat dict like this
        # as a normal form body when passed via `data=` (as opposed to
        # `json=`, which we use for Adyen instead - see AdyenAdapter).
        payload = {
            "mode": "payment",
            "client_reference_id": transaction.reference,
            "success_url": f"{transaction.return_url}?reference={transaction.reference}",
            "cancel_url": f"{transaction.return_url}?reference={transaction.reference}&canceled=true",
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": transaction.currency.lower(),
            "line_items[0][price_data][unit_amount]": amount_minor_units,
            "line_items[0][price_data][product_data][name]": f"Payment {transaction.reference}",
        }

        response_content = send_provider_api_request(
            "POST",
            STRIPE_CHECKOUT_SESSIONS_URL,
            headers={
                # Stripe authenticates with a plain Bearer token (the
                # tenant's own secret key), not an API-key-specific header -
                # this is standard OAuth2-style bearer auth.
                "Authorization": f"Bearer {config.stripe_secret_key}",
                "Stripe-Version": STRIPE_API_VERSION,
            },
            data=payload,
        )

        return {
            "redirect_url": response_content["url"],
            "provider_reference": response_content["id"],
            # response_content is already a plain, JSON-serializable dict -
            # send_provider_api_request() returns response.json() directly -
            # so unlike the old SDK-based version, there's no conversion
            # step needed before storing it in PaymentTransaction.provider_data.
            "raw_response": response_content,
        }

    def send_payment_request(self, transaction):
        """
        Mirrors Odoo's payment.transaction._send_payment_request().

        In Odoo, the redirect flow renders an HTML form via
        `_get_specific_rendering_values` + a QWeb template, which the
        customer's browser auto-submits to reach the provider's hosted
        page. We're a pure JSON API with no server-rendered HTML at all, so
        there's no form to render - we just hand `redirect_url` straight
        back to whoever called our API (see POST /payments/), and it's
        their job (a frontend, a mobile app, curl in the README) to send
        the end-customer's browser there.
        """
        return self.get_specific_processing_values(transaction)

    def send_capture_request(self, transaction):
        """Mirrors Odoo's _send_capture_request(). Implemented in Phase 3."""
        raise NotImplementedError("send_capture_request is implemented in Phase 3")

    def send_void_request(self, transaction):
        """Mirrors Odoo's _send_void_request(). Implemented in Phase 3."""
        raise NotImplementedError("send_void_request is implemented in Phase 3")

    def send_refund_request(self, transaction):
        """Mirrors Odoo's _send_refund_request(). Implemented in Phase 3."""
        raise NotImplementedError("send_refund_request is implemented in Phase 3")

    def verify_webhook_signature(self, raw_body, signature_header, webhook_secret):
        """
        Mirrors Odoo's payment_stripe module's webhook signature check
        (Stripe's own standard algorithm, documented in Stripe's own
        webhook docs - Odoo doesn't invent this, just implements it).

        Stripe signs each webhook by HMAC-SHA256-ing the string
        "{timestamp}.{raw request body}" with the tenant's webhook signing
        secret, then sends the result in the `Stripe-Signature` header as
        "t=<timestamp>,v1=<hex signature>" (Stripe can include more than
        one v1 value during a secret-rotation window - we only check the
        first one, which is enough for this project). We recompute the
        same HMAC ourselves and compare.

        `raw_body` MUST be the exact bytes Stripe sent, not a re-serialized
        version of a parsed dict - see StripeWebhookView in
        payments_core/views.py for why that matters and how we get it.

        Raises PaymentProviderRequestError (the view catches this and turns
        it into a 403) if the signature is missing, malformed, expired, or
        doesn't match - never returns False, since there's nothing useful a
        caller could do with a bare "no" here except immediately raise
        anyway.
        """
        if not signature_header:
            raise PaymentProviderRequestError("Missing Stripe-Signature header.")

        # Stripe-Signature looks like "t=1614556800,v1=abcdef...,v0=...".
        # Splitting on "," then on the first "=" gives us a dict of these
        # short keys -> values.
        parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
        timestamp = parts.get("t")
        signature = parts.get("v1")
        if not timestamp or not signature:
            raise PaymentProviderRequestError("Malformed Stripe-Signature header.")

        if time.time() - int(timestamp) > STRIPE_WEBHOOK_AGE_TOLERANCE:
            raise PaymentProviderRequestError("Stripe webhook timestamp is too old.")

        signed_payload = f"{timestamp}.{raw_body.decode()}"
        expected_signature = hmac.new(
            webhook_secret.encode(), signed_payload.encode(), hashlib.sha256
        ).hexdigest()

        # hmac.compare_digest (NOT `==`) - a plain `==` string comparison
        # stops as soon as it finds the first differing character, which
        # means how LONG the comparison takes leaks information about how
        # much of the signature we got right (a "timing attack").
        # compare_digest always takes the same amount of time regardless of
        # where the strings differ, closing that side channel.
        if not hmac.compare_digest(expected_signature, signature):
            raise PaymentProviderRequestError("Stripe webhook signature verification failed.")

        return True

    def search_by_reference(self, payment_data):
        """
        Mirrors Odoo's payment_stripe module's `_search_by_reference`.

        Looks for a `reference` key in payment_data - this project passes
        OUR OWN transaction reference through under that key (see
        StripeWebhookView / StripeReturnView in payments_core/views.py,
        which build it from Stripe's `client_reference_id` - the value we
        set when CREATING the Checkout Session in
        get_specific_processing_values() above), rather than trying to
        derive it from a Stripe-specific id.
        """
        reference = payment_data.get("reference")
        if not reference:
            logger.warning("Stripe: received payment_data with no reference.")
            return None

        tx = PaymentTransaction.objects.filter(
            reference=reference, provider__code="stripe"
        ).first()
        if tx is None:
            logger.warning("Stripe: no transaction found matching reference '%s'.", reference)
        return tx

    def apply_updates(self, transaction, payment_data):
        """
        Mirrors Odoo's payment.transaction._apply_updates() override in the
        payment_stripe module.

        Reads a Stripe PaymentIntent's `status` if we have one - preferred,
        since PaymentIntent status is Stripe's most precise signal for this
        - and falls back to reading a Checkout Session's `status` /
        `payment_status` pair if that's what we actually have instead. This
        project's return-URL and webhook handlers (see
        payments_core/views.py) both pass Checkout Session data in
        practice, since fetching a fully-expanded PaymentIntent would need
        an extra Stripe API call this phase doesn't need; the PaymentIntent
        branch exists for completeness and so this method works correctly
        either way, regardless of which object a caller has on hand.
        """
        payment_intent = payment_data.get("payment_intent")
        checkout_session = payment_data.get("checkout_session")

        if isinstance(payment_intent, dict) and payment_intent.get("status"):
            if payment_intent.get("id"):
                transaction.provider_reference = payment_intent["id"]

            transition_method_name = STRIPE_PAYMENT_INTENT_STATUS_TRANSITIONS.get(
                payment_intent["status"]
            )
            if transition_method_name is None:
                logger.warning(
                    "No state transition mapped for Stripe PaymentIntent status "
                    "'%s' on transaction %s",
                    payment_intent["status"],
                    transaction.reference,
                )
                return

            getattr(transaction, transition_method_name)(
                state_message=f"Stripe PaymentIntent status: {payment_intent['status']}"
            )
            return

        if isinstance(checkout_session, dict):
            if checkout_session.get("id"):
                transaction.provider_reference = checkout_session["id"]

            session_status = checkout_session.get("status")
            payment_status = checkout_session.get("payment_status")

            if session_status == "complete" and payment_status in ("paid", "no_payment_required"):
                transaction._set_done(
                    state_message=f"Stripe checkout.session status: {session_status}/{payment_status}"
                )
            elif session_status == "expired":
                transaction._set_canceled(state_message="Stripe checkout session expired")
            elif session_status == "open":
                # Still waiting on the customer - nothing to change; the
                # transaction should already be "pending" from POST /payments/.
                logger.info(
                    "Stripe checkout session %s still open - no state change.",
                    checkout_session.get("id"),
                )
            else:
                logger.warning(
                    "Unrecognized Stripe checkout session status/payment_status "
                    "combination ('%s', '%s') on transaction %s",
                    session_status,
                    payment_status,
                    transaction.reference,
                )
            return

        logger.warning(
            "Stripe apply_updates called with neither a payment_intent nor a "
            "checkout_session for transaction %s",
            transaction.reference,
        )

    def extract_amount_data(self, transaction, payment_data):
        """
        Mirrors Odoo's payment.transaction._extract_amount_data() override
        in payment_stripe.

        Pulls amount/currency out of whichever object we have (PaymentIntent
        or Checkout Session - same payload duality as apply_updates above)
        and converts from Stripe's minor units back to our major-unit
        Decimal representation via to_major_currency_units().
        """
        payment_intent = payment_data.get("payment_intent")
        checkout_session = payment_data.get("checkout_session")

        if isinstance(payment_intent, dict) and payment_intent.get("amount") is not None:
            minor_amount = payment_intent["amount"]
            currency = (payment_intent.get("currency") or "").upper()
        elif isinstance(checkout_session, dict) and checkout_session.get("amount_total") is not None:
            minor_amount = checkout_session["amount_total"]
            currency = (checkout_session.get("currency") or "").upper()
        else:
            return {"amount": None, "currency": None}

        return {
            "amount": to_major_currency_units(minor_amount, currency),
            "currency": currency,
        }

    def extract_token_values(self, transaction, payment_data):
        """Mirrors Odoo's _extract_token_values(). Implemented in Phase 3."""
        raise NotImplementedError("extract_token_values is implemented in Phase 3")
