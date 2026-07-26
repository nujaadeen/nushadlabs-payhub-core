from decimal import Decimal

import requests

from .exceptions import PaymentProviderRequestError

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


def to_major_currency_units(minor_amount, currency):
    """
    Mirrors Odoo's payment_utils.to_major_currency_units() - the inverse of
    to_minor_currency_units() above. Converts an int amount in a currency's
    smallest unit (what Stripe/Adyen's APIs report back to US, in webhooks
    and return-URL responses) into a Decimal in that currency's everyday
    major-unit representation - the same representation
    PaymentTransaction.amount stores, so the two can be compared directly.

    Returns a Decimal, not a float, for the same reason
    to_minor_currency_units takes a Decimal in the other direction: floats
    can't represent most decimal fractions exactly, which could make a
    perfectly correct amount look like a mismatch (or vice versa) purely
    from floating-point rounding when _validate_amount compares this
    against self.amount.
    """
    decimal_places = 0 if currency.upper() in ZERO_DECIMAL_CURRENCIES else 2
    return Decimal(minor_amount) / (10 ** decimal_places)


def send_provider_api_request(method, url, headers=None, data=None, json=None, timeout=30):
    """
    Mirrors Odoo's payment.provider._send_api_request().

    A single, generic wrapper around `requests.request(...)` that every
    adapter's HTTP calls should go through, instead of each adapter
    reimplementing its own try/except/raise_for_status dance. Both Stripe
    and Adyen adapters use this now - Stripe calls Stripe's REST API
    directly with `data=` (form-encoded, see StripeAdapter), Adyen calls
    its API with `json=` (JSON body, see AdyenAdapter); this helper doesn't
    care which, it just passes whichever one you give it straight through
    to `requests.request`.

    Returns the parsed JSON response body on success. Raises our own
    PaymentProviderRequestError - not a `requests`-specific exception - on
    any failure: a connection error, a timeout, or a non-2xx status code.
    That's the whole point of this helper existing: every adapter, and the
    view layer that calls them, only ever has to handle ONE exception type,
    no matter which provider or which kind of failure it was.
    """
    try:
        response = requests.request(
            method, url, headers=headers, data=data, json=json, timeout=timeout
        )
        # raise_for_status() turns a non-2xx response (bad credentials, a
        # malformed request, the provider being down) into a
        # requests.HTTPError - without this, an error response would
        # otherwise look exactly like a success to whoever called us, since
        # `requests` does NOT raise automatically just because the server
        # said "error".
        response.raise_for_status()
    except requests.RequestException as exc:
        # Try to surface the PROVIDER's own error message instead of just
        # requests' generic "400 Client Error: ..." text - Stripe and Adyen
        # both return a JSON body describing what actually went wrong even
        # on failure (e.g. Stripe's {"error": {"message": "..."}}), which
        # is far more useful for debugging than the bare HTTP status line.
        # `exc.response` only exists on HTTPError (raised by
        # raise_for_status() above) - a connection error or timeout never
        # got a response at all, so we fall back to the exception's own
        # message in that case.
        error_response = getattr(exc, "response", None)
        detail = str(exc)
        if error_response is not None:
            try:
                error_body = error_response.json()
            except ValueError:
                error_body = None
            if error_body:
                # Stripe nests its message under {"error": {"message": ...}};
                # Adyen puts it directly at {"message": ...}. Check
                # isinstance() before calling .get() on the nested "error"
                # value - if some other provider ever puts a plain STRING
                # there instead of a dict, blindly calling .get() on it
                # would raise its own AttributeError and mask the real
                # error we're trying to report.
                nested_error = error_body.get("error")
                message = nested_error.get("message") if isinstance(nested_error, dict) else None
                detail = message or error_body.get("message") or detail
        raise PaymentProviderRequestError(detail) from exc

    return response.json()
