# Currencies supported by Stripe.
#
# This mirrors the kind of constant Odoo keeps in its own
# payment_stripe/const.py file: a plain, hardcoded list baked into the
# adapter's own code, NOT something read from a database column or
# configured per-tenant (see StripeAdapter.get_supported_currencies in
# services.py, and PaymentProviderAdapter.get_supported_currencies in
# payments_core/interfaces.py, for the full "why a constant, not a field"
# reasoning).
#
# This is illustrative/simplified - Stripe actually supports most world
# currencies (~135 of them), but exhaustively listing all of them isn't
# necessary for this project, so this is a representative subset. This
# file is the ONE place to edit if you need to add support for a currency
# that isn't listed here yet.
SUPPORTED_CURRENCIES = [
    "USD",
    "EUR",
    "GBP",
    "AED",
    "AUD",
    "CAD",
    "JPY",
    "SGD",
    "INR",
]

# Stripe event `type` values where event["data"]["object"] is genuinely a
# Checkout Session (i.e. it actually has fields like client_reference_id,
# status, payment_status - see StripeAdapter.apply_updates/
# extract_amount_data in services.py, and StripeWebhookView in
# payments_core/views.py).
#
# Unlike Adyen (whose webhooks always carry the same notification item
# shape regardless of event), Stripe's data.object is a DIFFERENT kind of
# object depending on the event's `type` - a charge.succeeded event's
# object is a Charge, a payment_intent.succeeded event's object is a
# PaymentIntent, and NEITHER of those has a client_reference_id at all
# (that field only exists on a Checkout Session). Before this list existed,
# the webhook view read client_reference_id off every event's object
# regardless of type, which silently failed reference lookup for every
# non-Checkout-Session event.
#
# Mirrors Odoo's own payment_stripe module's HANDLED_WEBHOOK_EVENTS
# allowlist: only the event types listed here get a reference extracted
# and passed to PaymentTransaction._process() at all - anything else is
# acknowledged (logged + a normal 200) without attempting a lookup that
# would just fail.
HANDLED_WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
]
