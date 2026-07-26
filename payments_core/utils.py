# Currencies whose smallest unit IS the major unit - i.e. they have no
# "cents". Stripe and Adyen both require amounts in a currency's smallest
# unit ("minor units"): $145.50 has to be sent as 14550 (multiply by 100),
# but ¥145 has to be sent as just 145 (JPY has no sub-unit, so the
# multiplier is 1, not 100). Odoo looks this up from the `decimal_places`
# field on its res.currency model; we don't have a Currency model in this
# trimmed-down schema, so we hardcode the common zero-decimal currencies
# instead. This list is NOT exhaustive - good enough for a learning
# project, not production-grade.
ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW", "VND", "CLP", "ISK", "HUF"}


def to_minor_currency_units(amount, currency):
    """
    Mirrors Odoo's payment_utils.to_minor_currency_units().

    Converts a Decimal amount in a currency's everyday ("major unit")
    representation into an int in that currency's smallest ("minor unit")
    representation, which is the format Stripe's and Adyen's APIs expect.

    `amount` is always a Decimal here (see PaymentTransaction.amount, a
    DecimalField) rather than a float, specifically so this multiplication
    is exact - floats can't represent most decimal fractions precisely
    (e.g. 0.1 + 0.2 != 0.3 in float math), which would risk sending a
    provider a charge amount that's off by a cent.
    """
    decimal_places = 0 if currency.upper() in ZERO_DECIMAL_CURRENCIES else 2
    return int(amount * (10 ** decimal_places))
