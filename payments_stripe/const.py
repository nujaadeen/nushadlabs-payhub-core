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
