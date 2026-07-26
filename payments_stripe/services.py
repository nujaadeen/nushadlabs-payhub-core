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
