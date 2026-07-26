import base64
import hashlib
import hmac
import logging

from payments_core.exceptions import PaymentProviderRequestError
from payments_core.interfaces import PaymentProviderAdapter
from payments_core.utils import send_provider_api_request, to_major_currency_units, to_minor_currency_units

from . import const

# Importing payments_core.models at the top of this file is safe (does not
# create a circular import) for the same reason it's safe in
# payments_stripe/services.py - see that file's comment on this same
# import for the full explanation.
from payments_core.models import PaymentTransaction

logger = logging.getLogger(__name__)

# Adyen versions each API endpoint (v66, v68, v71, ...) and expects that
# version number baked right into the URL. Odoo keeps a whole const.py
# mapping of "API name" -> "version" so different endpoints can be bumped to
# newer versions independently over time; we hardcode a single version here
# to keep this simple, since this project only calls a couple of Adyen
# endpoints, all on the same version.
ADYEN_API_VERSION = "v71"
ADYEN_TEST_PAYMENTS_URL = f"https://checkout-test.adyen.com/{ADYEN_API_VERSION}/payments"
ADYEN_TEST_PAYMENTS_DETAILS_URL = f"https://checkout-test.adyen.com/{ADYEN_API_VERSION}/payments/details"

# The Adyen resultCodes this project currently knows how to react to, and
# which PaymentTransaction state-transition method each one maps to. This
# is a deliberately small subset of Odoo's full const.RESULT_CODES_MAPPING
# - just enough to cover the cases AdyenAdapter.apply_updates actually
# needs right now. Anything not in this dict is left alone (see
# apply_updates below) rather than guessed at.
ADYEN_RESULT_CODE_TRANSITIONS = {
    "Authorised": "_set_done",
    "Refused": "_set_error",
    "Error": "_set_error",
    "Cancelled": "_set_canceled",
}


def adyen_event_to_result_code(event_code, success):
    """
    Maps an Adyen webhook notification's (eventCode, success) pair to the
    equivalent `resultCode` our AdyenAdapter.apply_updates / the
    ADYEN_RESULT_CODE_TRANSITIONS mapping already knows how to react to.

    No leading underscore (unlike _compute_adyen_hmac_signature below,
    which stays private to this file) - AdyenWebhookView in
    payments_core/views.py needs to call this too, so it's part of this
    module's public surface rather than an internal implementation detail.

    Adyen's asynchronous webhook notifications use `eventCode` + a separate
    `success` flag instead of sending a `resultCode` directly the way the
    synchronous /payments and /payments/details responses do - so we
    translate one shape into the other here, right at the door, so the
    rest of the pipeline (_apply_updates) only ever has to understand
    `resultCode`, regardless of which of Adyen's three response mechanisms
    (see AdyenAdapter.get_specific_processing_values) actually produced it.

    Mirrors Odoo's own webhook event-code remapping. Returns None for an
    event code we don't have a mapping for at all, which tells the caller
    to skip that notification item entirely.
    """
    if event_code == "AUTHORISATION":
        return "Authorised" if success else "Refused"
    if event_code == "CANCELLATION":
        return "Cancelled" if success else "Error"
    if event_code in ("REFUND", "CAPTURE"):
        # TODO: Phase 4/5 - once refund/capture are real operations, a
        # successful notification here should update the CHILD transaction
        # they belong to (Odoo's _adyen_create_child_tx), not reuse
        # "Authorised" on the original transaction the way this does today.
        # This is a placeholder mapping that keeps the pipeline's SHAPE
        # correct without fully implementing behavior we haven't built.
        return "Authorised" if success else "Error"
    if event_code == "CAPTURE_FAILED":
        return "Error"
    return None


def _compute_adyen_hmac_signature(notification_item, hmac_key_hex):
    """
    Mirrors Odoo's payment_adyen `_compute_signature()` exactly - this is
    Adyen's own official HMAC signing algorithm (documented in Adyen's own
    webhook integration guide), not something Odoo invented.

    Adyen signs a notification by:
      1. Picking exactly 8 fields out of the notification - some of them
         nested, like amount.value/amount.currency, hence "flattening" the
         payload into dot-notation keys first, so nested and top-level
         fields can be looked up the same way.
      2. Escaping any literal backslash or colon INSIDE each field's value
         with a backslash - colons are about to become the separator
         BETWEEN fields, so a stray colon inside a value would otherwise
         make it ambiguous where one field ends and the next begins.
      3. Joining the 8 (now-escaped) values with ":".
      4. HMAC-SHA256-ing that joined string with the tenant's Adyen HMAC
         key - which Adyen gives you as a HEX string, not raw bytes, so it
         has to be hex-decoded first.
      5. Base64-encoding the result - this is the same format Adyen itself
         sends back to us in `additionalData.hmacSignature`, so encoding
         the same way lets us compare directly with `hmac.compare_digest`.
    """

    def flatten(d, prefix=""):
        flat = {}
        for key, value in d.items():
            full_key = f"{prefix}{key}"
            if isinstance(value, dict):
                flat.update(flatten(value, prefix=f"{full_key}."))
            else:
                flat[full_key] = value
        return flat

    flat_item = flatten(notification_item)

    fields = [
        "pspReference",
        "originalReference",
        "merchantAccountCode",
        "merchantReference",
        "amount.value",
        "amount.currency",
        "eventCode",
        "success",
    ]

    def escape(value):
        # Backslash first, THEN colon - if we escaped colon first, we'd
        # then also escape the backslash we just inserted FOR it, which
        # would double-escape incorrectly.
        text = "" if value is None else str(value)
        return text.replace("\\", "\\\\").replace(":", "\\:")

    signing_string = ":".join(escape(flat_item.get(field)) for field in fields)

    hmac_key_bytes = bytes.fromhex(hmac_key_hex)
    digest = hmac.new(hmac_key_bytes, signing_string.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


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
    payments_stripe/services.py for the full explanation (including the
    one non-abstract exception, get_supported_currencies, overridden below).

    Phase 2 implements the "online_redirect" flow only:
    get_specific_processing_values() (calls Adyen's /payments endpoint
    directly, via `requests`) and send_payment_request() (a thin wrapper
    around it). Every other method below is still a stub.
    """

    def get_supported_currencies(self):
        """
        Mirrors Odoo's payment_adyen module overriding
        payment.provider._get_supported_currencies() - same rationale as
        StripeAdapter.get_supported_currencies in
        payments_stripe/services.py. The actual list of codes lives in
        payments_adyen/const.py.
        """
        return const.SUPPORTED_CURRENCIES

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

    def verify_webhook_signature(self, notification_item, hmac_key):
        """
        Mirrors Odoo's payment_adyen webhook signature check.

        Unlike Stripe (which signs the WHOLE request body once), Adyen
        signs EACH notification item individually, with the signature
        living inside that item's own `additionalData.hmacSignature` field
        - so this method takes ONE notification item at a time (see
        AdyenWebhookView in payments_core/views.py, which loops over
        `data["notificationItems"]` and calls this once per item), not a
        raw request body.

        Raises PaymentProviderRequestError (the view catches this and skips
        just that one item - see AdyenWebhookView) if the signature is
        missing or doesn't match.
        """
        provided_signature = (notification_item.get("additionalData") or {}).get("hmacSignature")
        if not provided_signature:
            raise PaymentProviderRequestError("Missing Adyen hmacSignature.")

        expected_signature = _compute_adyen_hmac_signature(notification_item, hmac_key)

        # hmac.compare_digest, not `==` - see StripeAdapter.verify_webhook_signature
        # in payments_stripe/services.py for why (timing-attack resistance).
        if not hmac.compare_digest(expected_signature, provided_signature):
            raise PaymentProviderRequestError("Adyen webhook signature verification failed.")

        return True

    def search_by_reference(self, payment_data):
        """
        Mirrors Odoo's payment_adyen module's `_search_by_reference`.

        AUTHORISATION events (the only kind our own /payments call
        synchronously triggers, and the only kind we fully act on right
        now) are looked up by `merchantReference` - that's OUR OWN
        transaction reference, which we sent Adyen in the original
        /payments request's `reference` field, and Adyen echoes back under
        `merchantReference` in every notification about that payment.

        For any OTHER event code (REFUND, CAPTURE, CANCELLATION, ...), Odoo
        looks up by `pspReference` instead of `merchantReference` - because
        those operations get their OWN, DIFFERENT pspReference from Adyen
        (a refund is a distinct operation from the original payment, with
        its own id), and can even be INITIATED FROM ADYEN'S OWN DASHBOARD
        directly (not always through our API), so there's no guarantee our
        own reference is even present on that notification at all. Looking
        up by pspReference would mean searching CHILD transactions (each
        refund/capture is its own PaymentTransaction row, linked via
        source_transaction) - we haven't built that lookup, or
        refund/capture operations themselves, yet.

        # TODO: Phase 4/5 - once refund/capture/cancellation are real
        # operations (see PaymentTransaction._create_child_transaction),
        # look up the right CHILD transaction by provider_reference here
        # instead of just giving up.
        """
        event_code = payment_data.get("eventCode", "AUTHORISATION")

        if event_code != "AUTHORISATION":
            logger.warning(
                "Adyen: no lookup implemented yet for event code '%s' - skipping.", event_code
            )
            return None

        reference = payment_data.get("merchantReference")
        if not reference:
            logger.warning("Adyen: received AUTHORISATION data with no merchantReference.")
            return None

        tx = PaymentTransaction.objects.filter(reference=reference, provider__code="adyen").first()
        if tx is None:
            logger.warning(
                "Adyen: no transaction found matching merchantReference '%s'.", reference
            )
        return tx

    def apply_updates(self, transaction, payment_data):
        """
        Mirrors Odoo's payment.transaction._apply_updates() override in the
        payment_adyen module.

        Reads Adyen's `resultCode` and drives this project's state machine
        accordingly, using the small ADYEN_RESULT_CODE_TRANSITIONS mapping
        near the top of this file - a deliberately small subset of Odoo's
        full const.RESULT_CODES_MAPPING, just enough to cover the cases
        this project actually produces a resultCode for: the synchronous
        /payments and /payments/details responses (which send resultCode
        directly), and this project's webhook handler (which remaps
        eventCode+success into a resultCode via adyen_event_to_result_code
        before calling this - see AdyenWebhookView in
        payments_core/views.py). This method doesn't care WHERE
        `payment_data` came from, only what's in it.
        """
        # Mirrors Odoo updating provider_reference from the payment data -
        # keeps our record of "what Adyen calls this transaction" current.
        # This assignment alone does NOT hit the database - it's persisted
        # either by the state-transition call below (_set_done/_set_error
        # only saves the state-related fields, not this one) or by the
        # caller's own save() afterward (see PaymentCreateView and
        # PaymentTransaction._process in payments_core/models.py, both of
        # which save provider_reference explicitly after calling into the
        # adapter).
        if payment_data.get("pspReference"):
            transaction.provider_reference = payment_data["pspReference"]

        result_code = payment_data.get("resultCode")
        transition_method_name = ADYEN_RESULT_CODE_TRANSITIONS.get(result_code)

        if transition_method_name is None:
            # TODO: Phase 4/5 - Adyen has several more resultCodes (e.g.
            # "Pending", "PartiallyAuthorised") we don't have a state
            # transition for yet. Rather than guess at one, we log a
            # warning and leave the transaction's state exactly as it was -
            # safer than silently moving it somewhere wrong.
            logger.warning(
                "No state transition mapped for Adyen resultCode '%s' on transaction %s",
                result_code,
                transaction.reference,
            )
            return

        transition_method = getattr(transaction, transition_method_name)
        transition_method(state_message=f"Adyen resultCode: {result_code}")

    def extract_amount_data(self, transaction, payment_data):
        """
        Mirrors Odoo's payment.transaction._extract_amount_data() override
        in payment_adyen.

        Adyen always nests amount under
        {"amount": {"value": ..., "currency": ...}} - the same shape
        whether it's a synchronous /payments response, a webhook
        notification item, or a /payments/details response - so there's no
        "which shape do we have" branching needed here the way Stripe's
        version has.
        """
        amount = payment_data.get("amount") or {}
        minor_amount = amount.get("value")
        currency = amount.get("currency")

        if minor_amount is None or not currency:
            return {"amount": None, "currency": None}

        return {
            "amount": to_major_currency_units(minor_amount, currency),
            "currency": currency.upper(),
        }

    def extract_token_values(self, transaction, payment_data):
        """Mirrors Odoo's _extract_token_values(). Implemented in Phase 3."""
        raise NotImplementedError("extract_token_values is implemented in Phase 3")
