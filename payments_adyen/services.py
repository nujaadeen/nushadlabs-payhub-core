import logging

from payments_core.exceptions import PaymentProviderRequestError
from payments_core.interfaces import PaymentProviderAdapter
from payments_core.utils import send_provider_api_request, to_minor_currency_units

logger = logging.getLogger(__name__)

# Adyen versions each API endpoint (v66, v68, v71, ...) and expects that
# version number baked right into the URL. Odoo keeps a whole const.py
# mapping of "API name" -> "version" so different endpoints can be bumped to
# newer versions independently over time; we hardcode a single version here
# to keep this simple, since this project only calls one Adyen endpoint so
# far.
ADYEN_API_VERSION = "v71"
ADYEN_TEST_PAYMENTS_URL = f"https://checkout-test.adyen.com/{ADYEN_API_VERSION}/payments"

# The Adyen resultCodes this project currently knows how to react to, and
# which PaymentTransaction state-transition method each one maps to. This
# is a deliberately small subset of Odoo's full const.RESULT_CODES_MAPPING
# - just enough to cover the immediate-response case AdyenAdapter.apply_updates
# needs right now. Anything not in this dict is left alone (see
# apply_updates below) rather than guessed at.
ADYEN_RESULT_CODE_TRANSITIONS = {
    "Authorised": "_set_done",
    "Refused": "_set_error",
    "Error": "_set_error",
}


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

        Adyen's /payments response can come back in three different shapes,
        and this method has to distinguish between them (Odoo's actual
        payment_adyen module handles the same three cases):

          1. Direct/immediate response - a final `resultCode` (e.g.
             "Authorised", "Refused") right away, no further action needed.
          2. Additional action required (redirect) - an `action` object
             with a `url` for 3DS/redirect flows.
          3. Asynchronous webhook - Adyen calls back later with
             `notificationItems`. This never comes through THIS response at
             all (it's a separate HTTP request Adyen makes to US, later),
             so it's not something this method can see - that's Phase 3.
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

        data = send_provider_api_request(
            "POST",
            ADYEN_TEST_PAYMENTS_URL,
            headers={
                "X-API-Key": config.adyen_api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        # Case 2 - additional action required: check this FIRST, since a
        # response can technically carry both an `action` and a
        # `resultCode` (e.g. resultCode="RedirectShopperRequired") -
        # a redirect always means "the shopper still has more to do", so it
        # takes priority over resultCode.
        action = data.get("action") or {}
        redirect_url = action.get("url") if action.get("type") == "redirect" else None
        if redirect_url:
            return {
                "redirect_url": redirect_url,
                "provider_reference": data.get("pspReference"),
                # `data` is already a plain dict (response.json() parses the
                # JSON body straight into Python dicts/lists/primitives),
                # so - unlike Stripe's old SDK object - there's no
                # conversion needed before storing it in
                # PaymentTransaction.provider_data.
                "raw_response": data,
            }

        # Case 1 - direct/immediate response: Adyen has already reached a
        # final result, with nothing further needed from the shopper.
        # Odoo's controller calls tx_sudo._process("adyen",
        # dict(response_content, merchantReference=reference)) immediately
        # after this same /payments call, rather than waiting for a webhook
        # to report the same result later - the Phase 3 webhook will
        # eventually notify us of this too, but there's no reason to make
        # the API caller wait for it when Adyen already told us
        # synchronously. This is exactly why _apply_updates() is written to
        # be safely callable from more than one entry point (here, and
        # later from the Phase 3 webhook handler) - "processing" a
        # transaction isn't tied to any one trigger.
        if data.get("resultCode"):
            transaction._apply_updates(data)
            return {
                "redirect_url": None,
                "provider_reference": data.get("pspReference"),
                "status": "processed_immediately",
                "raw_response": data,
            }

        # Neither an `action` nor a `resultCode` - not one of the two
        # response shapes we can get from THIS call (case 3, the webhook,
        # is a separate request entirely - see the docstring above). This
        # is a genuinely unexpected response shape, so it's an error.
        raise PaymentProviderRequestError(
            "Adyen response contained neither an 'action' nor a 'resultCode' "
            "- unexpected response shape."
        )

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
        """
        Mirrors Odoo's payment.transaction._apply_updates() override in the
        payment_adyen module.

        Reads Adyen's `resultCode` and drives this project's state machine
        accordingly, using the small ADYEN_RESULT_CODE_TRANSITIONS mapping
        near the top of this file - a deliberately small subset of Odoo's
        full const.RESULT_CODES_MAPPING, just enough to cover the
        immediate-response case get_specific_processing_values() actually
        exercises this phase. Also called (with a richer payload) from
        Phase 3's webhook handler once that exists - this method doesn't
        care WHERE `payment_data` came from, only what's in it.
        """
        # Mirrors Odoo updating provider_reference from the payment data -
        # keeps our record of "what Adyen calls this transaction" current.
        # This assignment alone does NOT hit the database - it's persisted
        # either by the state-transition call below (_set_done/_set_error
        # only saves the state-related fields, not this one) or by the
        # caller's own save() afterward (see PaymentCreateView in
        # payments_core/views.py, which saves provider_reference right
        # after calling _get_processing_values()).
        if payment_data.get("pspReference"):
            transaction.provider_reference = payment_data["pspReference"]

        result_code = payment_data.get("resultCode")
        transition_method_name = ADYEN_RESULT_CODE_TRANSITIONS.get(result_code)

        if transition_method_name is None:
            # TODO: expand this mapping in Phase 3 - Adyen has several more
            # resultCodes (e.g. "Pending", "Cancelled",
            # "PartiallyAuthorised") we don't have a state transition for
            # yet. Rather than guess at one, we log a warning and leave the
            # transaction's state exactly as it was - safer than silently
            # moving it somewhere wrong.
            logger.warning(
                "No state transition mapped for Adyen resultCode '%s' on transaction %s",
                result_code,
                transaction.reference,
            )
            return

        transition_method = getattr(transaction, transition_method_name)
        transition_method(state_message=f"Adyen resultCode: {result_code}")

    def extract_amount_data(self, transaction, payment_data):
        """Mirrors Odoo's _extract_amount_data(). Implemented in Phase 3."""
        raise NotImplementedError("extract_amount_data is implemented in Phase 3")

    def extract_token_values(self, transaction, payment_data):
        """Mirrors Odoo's _extract_token_values(). Implemented in Phase 3."""
        raise NotImplementedError("extract_token_values is implemented in Phase 3")
