import stripe

from payments_core.exceptions import PaymentProviderRequestError
from payments_core.interfaces import PaymentProviderAdapter
from payments_core.utils import to_minor_currency_units


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
        """
        # `transaction.provider.stripe_config` reaches across the
        # OneToOneField from payments_stripe/models.py
        # (PaymentProviderStripeConfig.provider, related_name="stripe_config")
        # to get this tenant's Stripe credentials for this specific
        # provider row.
        config = transaction.provider.stripe_config

        # We set stripe.api_key on the `stripe` module itself, right here,
        # rather than once at import time in settings.py - credentials are
        # per-tenant, per-provider in this project (every PaymentProvider
        # row has its own secret key), so the right key has to be set
        # immediately before we use it, not globally at process startup.
        stripe.api_key = config.stripe_secret_key

        amount_minor_units = to_minor_currency_units(transaction.amount, transaction.currency)

        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=[
                    {
                        "price_data": {
                            "currency": transaction.currency.lower(),
                            "unit_amount": amount_minor_units,
                            "product_data": {"name": f"Payment {transaction.reference}"},
                        },
                        "quantity": 1,
                    }
                ],
                # Stripe echoes this value back to us on the Checkout
                # Session and on webhook events - using our own reference
                # here (instead of Stripe's session id) is what will let
                # Phase 3's webhook handler look up the right
                # PaymentTransaction row via _search_by_reference().
                client_reference_id=transaction.reference,
                success_url=f"{transaction.return_url}?reference={transaction.reference}",
                cancel_url=f"{transaction.return_url}?reference={transaction.reference}&canceled=true",
            )
        except stripe.error.StripeError as exc:
            # The Stripe SDK raises its own exception hierarchy
            # (stripe.error.StripeError and subclasses) for anything that
            # goes wrong - bad credentials, network failure, an invalid
            # request, Stripe being down, etc. We catch that one broad base
            # class and re-raise it as our own PaymentProviderRequestError,
            # so the view layer only ever needs to know about ONE exception
            # type, regardless of whether Stripe or Adyen is the one that
            # failed.
            raise PaymentProviderRequestError(str(exc)) from exc

        return {
            "redirect_url": session.url,
            "provider_reference": session.id,
            # session.to_dict() converts Stripe's response object into a
            # plain, JSON-serializable dict (the object itself is a
            # StripeObject, not a plain dict, and can't be stored directly
            # in a Postgres JSONField) so it can be saved as-is into
            # PaymentTransaction.provider_data for later inspection/
            # debugging.
            "raw_response": session.to_dict(),
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
        """Mirrors Odoo's _apply_updates(). Implemented in Phase 3."""
        raise NotImplementedError("apply_updates is implemented in Phase 3")

    def extract_amount_data(self, transaction, payment_data):
        """Mirrors Odoo's _extract_amount_data(). Implemented in Phase 3."""
        raise NotImplementedError("extract_amount_data is implemented in Phase 3")

    def extract_token_values(self, transaction, payment_data):
        """Mirrors Odoo's _extract_token_values(). Implemented in Phase 3."""
        raise NotImplementedError("extract_token_values is implemented in Phase 3")
