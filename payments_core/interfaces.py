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
    def verify_webhook_signature(self, request):
        """Verifies an incoming webhook actually came from the provider. Implemented in Phase 3."""
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
