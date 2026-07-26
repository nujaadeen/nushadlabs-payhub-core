from payments_core.interfaces import PaymentProviderAdapter
from payments_core.utils import send_provider_api_request, to_minor_currency_units

# Stripe versions its whole API by date and expects that date sent as the
# Stripe-Version header on every request (there's no version number in the
# URL itself, unlike Adyen). Pinning a specific version here - instead of
# leaving it unset, which would silently use whatever Stripe's CURRENT
# default is - means Stripe can't change this endpoint's response shape out
# from under us later without us noticing (a version bump would be a
# deliberate, visible change to this one line).
STRIPE_API_VERSION = "2023-10-16"
STRIPE_CHECKOUT_SESSIONS_URL = "https://api.stripe.com/v1/checkout/sessions"


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

    def verify_webhook_signature(self, request):
        """Verifies an incoming webhook actually came from Stripe. Implemented in Phase 3."""
        raise NotImplementedError("verify_webhook_signature is implemented in Phase 3")

    def search_by_reference(self, payment_data):
        """Mirrors Odoo's _search_by_reference(). Implemented in Phase 3."""
        raise NotImplementedError("search_by_reference is implemented in Phase 3")

    def apply_updates(self, transaction, payment_data):
        """
        Mirrors Odoo's _apply_updates(). Implemented in Phase 3.

        Stripe's flow in this project stays fully redirect-based for now
        (see get_specific_processing_values above) - unlike Adyen, Stripe's
        Checkout Session response never contains a final result on its own,
        so there's no "immediate result" case to handle synchronously here.
        This won't be exercised until Phase 3 wires up Stripe's webhook
        handling, which is what actually tells us how the payment went.
        """
        # TODO: Phase 3
        raise NotImplementedError("apply_updates is implemented in Phase 3")

    def extract_amount_data(self, transaction, payment_data):
        """Mirrors Odoo's _extract_amount_data(). Implemented in Phase 3."""
        raise NotImplementedError("extract_amount_data is implemented in Phase 3")

    def extract_token_values(self, transaction, payment_data):
        """Mirrors Odoo's _extract_token_values(). Implemented in Phase 3."""
        raise NotImplementedError("extract_token_values is implemented in Phase 3")
