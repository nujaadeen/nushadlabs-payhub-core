from abc import ABC, abstractmethod

# An ABC (Abstract Base Class) is Python's way of declaring "here is a
# contract that subclasses MUST fulfil". You can't instantiate an ABC
# directly, and if a subclass forgets to implement one of the methods marked
# `@abstractmethod`, Python raises a TypeError the moment you try to
# instantiate that subclass - instead of failing later, confusingly, the
# first time that missing method happens to be called.
#
# We use one here because every payment provider adapter (payments_stripe,
# payments_adyen, and any future provider) needs to expose the exact same
# set of operations to the core payment pipeline in payments_core. The core
# pipeline should never need to know "am I talking to Stripe or Adyen right
# now" - it just calls these methods on whichever adapter is configured for
# the transaction's provider. This is the "thin adapter" pattern: all
# provider-specific logic is hidden behind this one shared interface.


class PaymentProviderAdapter(ABC):
    """
    Contract every payment provider adapter must implement.

    Method names mirror the equivalent private methods on Odoo's
    payment.provider / payment.transaction models (noted per-method below).
    None of these have real implementations yet - Phase 0 is scaffolding
    only. Each raises NotImplementedError with a note on which phase will
    implement it.
    """

    @abstractmethod
    def send_payment_request(self, transaction):
        """Mirrors Odoo's payment.transaction._send_payment_request()."""
        raise NotImplementedError(
            "send_payment_request is implemented per-adapter in Phase 2"
        )

    @abstractmethod
    def send_capture_request(self, transaction):
        """Mirrors Odoo's payment.transaction._send_capture_request()."""
        raise NotImplementedError(
            "send_capture_request is implemented per-adapter in Phase 2"
        )

    @abstractmethod
    def send_void_request(self, transaction):
        """Mirrors Odoo's payment.transaction._send_void_request()."""
        raise NotImplementedError(
            "send_void_request is implemented per-adapter in Phase 2"
        )

    @abstractmethod
    def send_refund_request(self, transaction):
        """Mirrors Odoo's payment.transaction._send_refund_request()."""
        raise NotImplementedError(
            "send_refund_request is implemented per-adapter in Phase 2"
        )

    @abstractmethod
    def get_specific_processing_values(self, transaction):
        """Mirrors Odoo's payment.transaction._get_specific_processing_values()."""
        raise NotImplementedError(
            "get_specific_processing_values is implemented per-adapter in Phase 2"
        )

    @abstractmethod
    def verify_webhook_signature(self, *args, **kwargs):
        """
        Verifies an incoming webhook actually came from the provider, not
        an attacker who just knows our webhook URL.

        Unlike every other method on this interface, the exact parameters
        here genuinely differ per adapter, because Stripe and Adyen sign
        their webhooks in fundamentally different ways: Stripe computes ONE
        HMAC over the whole raw request body, so its version takes
        (raw_body, signature_header, webhook_secret) - see
        StripeAdapter.verify_webhook_signature in payments_stripe/services.py.
        Adyen signs EACH notification item individually using specific
        fields out of that item, so its version takes
        (notification_item, hmac_key) instead - see
        AdyenAdapter.verify_webhook_signature in payments_adyen/services.py.
        This method is declared with `*args, **kwargs` here (rather than a
        made-up shared signature that wouldn't actually match either
        implementation) specifically to acknowledge that difference - the
        real contract is just "every adapter must expose a method with this
        name that raises PaymentProviderRequestError on a bad signature",
        not "with these exact parameters".
        """
        raise NotImplementedError(
            "verify_webhook_signature is implemented per-adapter in Phase 3"
        )

    @abstractmethod
    def search_by_reference(self, payment_data):
        """Mirrors Odoo's payment.transaction._search_by_reference(). Implemented in Phase 3."""
        raise NotImplementedError(
            "search_by_reference is implemented per-adapter in Phase 3"
        )

    @abstractmethod
    def apply_updates(self, transaction, payment_data):
        """Mirrors Odoo's payment.transaction._apply_updates(). Implemented in Phase 3."""
        raise NotImplementedError(
            "apply_updates is implemented per-adapter in Phase 3"
        )

    @abstractmethod
    def extract_amount_data(self, transaction, payment_data):
        """Mirrors Odoo's payment.transaction._extract_amount_data(). Implemented in Phase 3."""
        raise NotImplementedError(
            "extract_amount_data is implemented per-adapter in Phase 3"
        )

    @abstractmethod
    def extract_token_values(self, transaction, payment_data):
        """Mirrors Odoo's payment.transaction._extract_token_values(). Implemented in Phase 3."""
        raise NotImplementedError(
            "extract_token_values is implemented per-adapter in Phase 3"
        )

    # ------------------------------------------------------------------
    # get_supported_currencies is deliberately NOT decorated with
    # @abstractmethod, unlike every method above. Every other method here
    # represents something an adapter MUST actively implement to do
    # anything useful (there's no sensible default for "how do I send a
    # payment request"). This one is different: Odoo's own
    # payment.provider._get_supported_currencies() has a real, useful BASE
    # behavior - "if a provider doesn't say otherwise, assume it supports
    # every currency" - and individual provider modules override it only
    # when they need to restrict that. We mirror the same shape: this
    # concrete method IS that base/default behavior, inherited as-is by any
    # adapter that doesn't need to restrict its currencies, and overridden
    # by ones that do (see StripeAdapter.get_supported_currencies /
    # AdyenAdapter.get_supported_currencies for real overrides).
    # ------------------------------------------------------------------
    def get_supported_currencies(self):
        """
        Return the list of ISO 4217 currency codes this adapter supports.

        Mirrors Odoo's PaymentProvider._get_supported_currencies(). The
        default behavior (this base implementation, used by any adapter
        that doesn't override it) is to support ALL currencies - we
        represent "no restriction" as `None` here rather than returning
        some impossibly long exhaustive list of every currency in
        existence. This is the same null-means-unrestricted convention
        you'd reach for on a database field too (see
        ProviderListCreateView's currency filter in payments_core/views.py
        for exactly how `None` is treated there) - it just lives in code,
        as a method's return value, instead of in a column, because WHICH
        currencies a provider supports is a property of the adapter's own
        integration code here, not something a tenant configures per row.
        Adapters that only support a specific subset should override this
        and return an explicit list instead.
        """
        return None
