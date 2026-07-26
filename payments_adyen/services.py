import requests

from payments_core.exceptions import PaymentProviderRequestError
from payments_core.interfaces import PaymentProviderAdapter
from payments_core.utils import to_minor_currency_units

# Adyen versions each API endpoint (v66, v68, v71, ...) and expects that
# version number baked right into the URL. Odoo keeps a whole const.py
# mapping of "API name" -> "version" so different endpoints can be bumped to
# newer versions independently over time; we hardcode a single version here
# to keep this simple, since this project only calls one Adyen endpoint so
# far.
ADYEN_API_VERSION = "v71"
ADYEN_TEST_PAYMENTS_URL = f"https://checkout-test.adyen.com/{ADYEN_API_VERSION}/payments"


def get_feature_support_fields(provider):
    """
    Adyen's override of PaymentProvider._compute_feature_support_fields().

    Mirrors Odoo's pattern where the payment_adyen module overrides
    payment.provider._compute_feature_support_fields() to declare what
    Adyen actually supports. Same override-by-function pattern as
    payments_stripe/services.py.get_feature_support_fields() - see that
    file's docstring for the full explanation.

    Nothing beyond payment creation is implemented for Adyen yet, so every
    key below stays "not_implemented".

    # Phase 2/3 TODO: once send_capture_request / send_refund_request /
    # tokenization / express checkout are actually implemented for Adyen,
    # flip the matching key here (e.g. support_refund = "full") - this
    # function is the one place that needs to change.
    """
    support_fields = provider._compute_feature_support_fields()
    # No overrides yet - see the TODO above.
    return support_fields


class AdyenAdapter(PaymentProviderAdapter):
    """
    Adyen's implementation of the PaymentProviderAdapter contract
    (payments_core/interfaces.py). Same "must override every abstract
    method to even be instantiable" rule applies here as it does for
    StripeAdapter - see that class's docstring in
    payments_stripe/services.py for the full explanation.

    Phase 2 implements the "online_redirect" flow only:
    get_specific_processing_values() (calls Adyen's /payments endpoint
    directly, via `requests`) and send_payment_request() (a thin wrapper
    around it). Every other method below is still a stub.
    """

    def get_specific_processing_values(self, transaction):
        """
        Mirrors Odoo's payment_adyen module's `_send_payment_request` /
        the underlying `_adyen_make_request` call to Adyen's /payments
        endpoint, simplified to the "online_redirect" flow only.

        Unlike Stripe (which has a dedicated Checkout Session product for
        the hosted-redirect case), Adyen's /payments endpoint is the SAME
        endpoint used for embedded card payments too - it just responds
        differently depending on what payment method info you send it. A
        real "online_direct" (embedded card fields) integration would send
        actual card details in `paymentMethod` and get a result back
        directly, no redirect. We haven't built that flow (out of scope
        this phase - see payments_adyen app docs), so calling this against
        a real Adyen sandbox key without proper paymentMethod details will
        likely get an error response from Adyen - and that's fine: it comes
        back to us as a PaymentProviderRequestError, same as any other
        failure.
        """
        config = transaction.provider.adyen_config
        amount_minor_units = to_minor_currency_units(transaction.amount, transaction.currency)

        payload = {
            "merchantAccount": config.adyen_merchant_account,
            "amount": {
                "value": amount_minor_units,
                "currency": transaction.currency.upper(),
            },
            "reference": transaction.reference,
            "returnUrl": f"{transaction.return_url}?reference={transaction.reference}",
        }

        try:
            response = requests.post(
                ADYEN_TEST_PAYMENTS_URL,
                json=payload,
                headers={
                    "X-API-Key": config.adyen_api_key,
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            # raise_for_status() turns any non-2xx response (bad
            # credentials, malformed request, Adyen-side error) into a
            # requests.HTTPError - without this, a 4xx/5xx response would
            # silently fall through as if the call had succeeded, since
            # requests does NOT raise automatically just because the
            # server said "error".
            response.raise_for_status()
        except requests.RequestException as exc:
            # requests.RequestException is the base class for every error
            # the `requests` library can raise on its own (connection
            # failures, timeouts) AND for the HTTPError raised by
            # raise_for_status() above. Same reasoning as StripeAdapter:
            # normalize to our own exception type so the view layer doesn't
            # need to know which library/adapter raised the original error.
            raise PaymentProviderRequestError(str(exc)) from exc

        data = response.json()
        # For payment methods that need the shopper to be redirected
        # somewhere (3D Secure, iDEAL, most non-card methods), Adyen
        # responds with an `action` object describing that next step
        # instead of a final result. We only handle the redirect case this
        # phase.
        action = data.get("action") or {}
        redirect_url = action.get("url") if action.get("type") == "redirect" else None

        if not redirect_url:
            # TODO: handle direct/non-redirect Adyen responses in a later
            # phase. Adyen can also return a final result with no redirect
            # at all (e.g. instant refusal, or a payment method that
            # doesn't need one) - we haven't built handling for that, so we
            # treat "no redirect in the response" as an error for now
            # rather than silently returning an incomplete result to the
            # caller.
            raise PaymentProviderRequestError(
                "Adyen response did not include a redirect action - "
                "non-redirect Adyen responses aren't handled yet."
            )

        return {
            "redirect_url": redirect_url,
            "provider_reference": data.get("pspReference"),
            # `data` is already a plain dict (response.json() parses the
            # JSON body straight into Python dicts/lists/primitives), so -
            # unlike Stripe's SDK object - there's no conversion needed
            # before storing it in PaymentTransaction.provider_data.
            "raw_response": data,
        }

    def send_payment_request(self, transaction):
        """
        Mirrors Odoo's payment.transaction._send_payment_request(). Same
        simplification as StripeAdapter.send_payment_request() - see that
        method's docstring in payments_stripe/services.py.
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
        """Verifies an incoming webhook actually came from Adyen. Implemented in Phase 3."""
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
