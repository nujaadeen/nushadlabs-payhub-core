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
