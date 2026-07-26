# Currencies supported by Adyen - same rationale as
# payments_stripe/const.py (see that file's comment for the full "hardcoded
# constant, not a database field" explanation).
#
# Adyen also supports a broad set of currencies in reality; this is a
# representative subset, not an exhaustive list. This file is the ONE
# place to edit if you need to add support for a currency that isn't
# listed here yet.
SUPPORTED_CURRENCIES = [
    "USD",
    "EUR",
    "GBP",
    "AED",
    "AUD",
    "CAD",
    "SEK",
    "NOK",
]
