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

# Keys to mask (case-insensitively) before logging anything that might
# contain them - see payments_stripe/const.py's SENSITIVE_KEYS for the
# same idea applied to Stripe, and payments_core/logging_utils.py's
# mask_sensitive() for how this list actually gets used. Covers Adyen's
# real credential fields both ways they can show up: the short
# request-body key name (from AdyenConfigInputSerializer/
# ADYEN_CONFIG_FIELD_MAP in payments_core/serializers.py) and the
# prefixed model field name (from PaymentProviderAdyenConfig in
# payments_adyen/models.py) - plus "x-api-key", since
# AdyenAdapter._get_specific_processing_values sends the API key as an
# `X-API-Key` HEADER, not a body field, so that header needs masking too
# if headers are ever logged. Deliberately does NOT include
# "merchant_account"/"adyen_merchant_account" or "theme_id"/
# "adyen_theme_id" - neither identifies a secret, they're both more like
# account/config identifiers than credentials.
SENSITIVE_KEYS = [
    "api_key",
    "adyen_api_key",
    "hmac_key",
    "adyen_hmac_key",
    "client_key",
    "adyen_client_key",
    "x-api-key",
]
