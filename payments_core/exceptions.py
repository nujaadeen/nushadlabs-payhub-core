class PaymentProviderRequestError(Exception):
    """
    Raised when a call to an actual payment provider's API (Stripe, Adyen,
    ...) fails - a network error, a timeout, a non-2xx response, or a
    response we don't know how to handle.

    Kept as a single, provider-agnostic exception type (rather than one
    exception class per adapter) so the code that CATCHES it - the
    POST /payments/ view - doesn't need to know or care which provider
    failed. It just needs to know "the external call didn't work" and reacts
    the same way (set the transaction to `error` with the message) no matter
    which adapter raised it. Each adapter is responsible for catching its
    own SDK/library-specific exception (e.g. stripe.error.StripeError,
    requests.RequestException) and re-raising it as this type.
    """
