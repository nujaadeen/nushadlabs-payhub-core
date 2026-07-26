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
#
# NOTE: this project no longer calls plain /payments (see
# get_specific_processing_values below for why) - only /payments/details
# (the 3DS-continuation endpoint used by AdyenPaymentsDetailsView/
# AdyenReturnView in payments_core/views.py) still uses this version.
ADYEN_API_VERSION = "v71"
ADYEN_TEST_PAYMENTS_DETAILS_URL = f"https://checkout-test.adyen.com/{ADYEN_API_VERSION}/payments/details"

# The Sessions endpoint used by get_specific_processing_values below - see
# that method's docstring for why this project uses Sessions (specifically
# Hosted Checkout mode) instead of the raw /payments endpoint above.
# Pinned to a NEWER version than ADYEN_API_VERSION deliberately: Adyen's
# Hosted Checkout mode (the "mode": "hosted" field in the request, and the
# "url" field this method reads from the response) requires API v72 or
# later per Adyen's own Hosted Checkout integration docs, checked directly
# against Adyen's current documentation while building this - v71 (pinned
# above, still correct for the untouched /payments/details endpoint) does
# not support it. Two different pinned versions in one file looks odd at
# first glance, but each is pinned to what its own endpoint actually
# requires, exactly like ADYEN_API_VERSION's own comment describes for why
# versions are pinned at all.
ADYEN_SESSIONS_API_VERSION = "v72"
ADYEN_SESSIONS_URL = f"https://checkout-test.adyen.com/{ADYEN_SESSIONS_API_VERSION}/sessions"

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
        Uses Adyen's Sessions API (Hosted Checkout mode), NOT Odoo's
        payment_adyen approach of calling /payments directly - a deliberate,
        documented exception to "follow Odoo exactly", not a workaround.

        WHY we deviate from Odoo here: Odoo's payment_adyen module calls
        Adyen's raw /payments endpoint because Odoo ships a frontend
        (Adyen's own Drop-in/Components JS) that collects the shopper's
        chosen payment method details BEFORE that call happens - /payments
        requires a `paymentMethod` object describing exactly that, and
        without one it fails with "Required object 'paymentMethod' is not
        provided". This project is a backend-only, redirect-first API with
        no frontend of our own to collect that - there's no request-body
        fix that makes /payments work for us, because we structurally can't
        satisfy what it actually requires.

        Instead we use Adyen's Sessions API with `"mode": "hosted"` - this
        is Adyen's equivalent of Stripe Checkout Sessions above: Adyen
        hosts the ENTIRE payment page itself (shopper picks a card, enters
        details, etc. all on Adyen's own page) and hands back a ready-made
        redirect URL in the response, so - like Stripe - we never need to
        collect or even see any payment method details ourselves. Confirmed
        directly against Adyen's current Hosted Checkout integration docs
        while building this (not guessed): the /sessions response for
        "mode": "hosted" includes a `url` field that IS the finished
        redirect URL - no further construction needed on our end.

        Two real prerequisites this mode has, beyond a normal Sessions call
        (also confirmed against Adyen's current docs, not guessed):
          - API v72 or later - see ADYEN_SESSIONS_API_VERSION's comment
            above for why this differs from ADYEN_API_VERSION.
          - A `themeId` - see PaymentProviderAdyenConfig.adyen_theme_id in
            payments_adyen/models.py for what this is and why it can't be
            auto-provisioned by this method.

        This method no longer needs to distinguish between multiple
        /payments response shapes (the old three-case immediate-result /
        action / webhook-only handling this replaced) - Hosted Checkout
        always responds with a redirect URL; there's no "immediate result,
        no redirect needed" case here the way raw /payments had, since the
        shopper always finishes the payment on Adyen's own hosted page.
        """
        config = transaction.provider.adyen_config
        if not config.adyen_theme_id:
            raise PaymentProviderRequestError(
                "This Adyen provider has no adyen_theme_id configured - "
                "Hosted Checkout requires a theme created in the Adyen "
                "Customer Area (Pay by Link > Themes) first. See "
                "PaymentProviderAdyenConfig.adyen_theme_id."
            )

        amount_minor_units = to_minor_currency_units(transaction.amount, transaction.currency)

        payload = {
            "merchantAccount": config.adyen_merchant_account,
            "amount": {
                "value": amount_minor_units,
                "currency": transaction.currency.upper(),
            },
            "reference": transaction.reference,
            "returnUrl": f"{transaction.return_url}?reference={transaction.reference}",
            "mode": "hosted",
            "themeId": config.adyen_theme_id,
            # countryCode is optional (per Adyen's docs) and would normally
            # improve the shopper's hosted-page experience (localized
            # payment methods etc.) - omitted entirely here because nothing
            # in this project currently captures a country for a tenant,
            # customer, or transaction to source it from.
        }

        data = send_provider_api_request(
            "POST",
            ADYEN_SESSIONS_URL,
            headers={
                "X-API-Key": config.adyen_api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        redirect_url = data.get("url")
        if not redirect_url:
            # Hosted Checkout is documented to always return `url` - not
            # having one is a genuinely unexpected response shape, the same
            # way the old code treated "neither action nor resultCode" as
            # an error rather than guessing at a fallback.
            raise PaymentProviderRequestError(
                "Adyen /sessions response (mode=hosted) did not include a "
                "'url' to redirect the shopper to - unexpected response "
                "shape."
            )

        return {
            "redirect_url": redirect_url,
            # The session id, not a pspReference - Sessions doesn't hand us
            # a pspReference until the shopper actually pays (it'll arrive
            # later via the webhook's AUTHORISATION notification, same as
            # Stripe's flow only having a Checkout Session id up front, not
            # a PaymentIntent id). Matches the pattern
            # StripeAdapter.get_specific_processing_values uses for storing
            # its own Checkout Session id as provider_reference.
            "provider_reference": data.get("id"),
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
        /payments/details response (which sends resultCode directly - see
        AdyenPaymentsDetailsView/AdyenReturnView in payments_core/views.py),
        and this project's webhook handler (which remaps eventCode+success
        into a resultCode via adyen_event_to_result_code before calling
        this - see AdyenWebhookView in payments_core/views.py). This method
        doesn't care WHERE `payment_data` came from, only what's in it -
        which is exactly why switching get_specific_processing_values above
        to the Sessions API (no more synchronous resultCode from creation
        itself, only from the webhook or /payments/details afterward)
        needed no changes here at all: it was already payload-shape-driven,
        not call-site-driven, verified by rereading it against Adyen's
        current webhook notification format while making that change.
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
