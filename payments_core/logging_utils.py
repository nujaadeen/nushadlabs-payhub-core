import logging

# Mirrors Odoo's get_payment_logger() helper, which wraps the standard
# logger and masks a SENSITIVE_KEYS list out of anything logged through
# it - but SIMPLIFIED and EXPLICIT here, not automatic. Odoo's real
# version hooks into Python's standard `logging` machinery globally (a
# logging.Filter attached to handlers), so EVERY log call anywhere in
# Odoo's payment modules gets masked automatically, without any call site
# having to remember to do anything special. That's powerful, but hard to
# trace for a beginner: the masking happens somewhere else entirely from
# the log call itself, invisibly.
#
# This project does the SAME masking, but EXPLICITLY: call mask_sensitive()
# yourself, right before you log something that might contain sensitive
# data. Nothing here hooks into Python's logging system globally - if a
# call site forgets to call mask_sensitive(), that specific log call just
# won't be masked (a real trade-off against Odoo's automatic approach),
# but in exchange, reading any ONE log call in this codebase tells you the
# whole story: either it masks its own data right there, or it doesn't
# touch anything sensitive at all.


def get_masked_logger(name, sensitive_keys=None):
    """
    Returns a standard Python logger for `name` - exactly what
    `logging.getLogger(name)` already gives you; this function doesn't
    wrap or replace anything about how Python's logging module works.

    The one thing it adds: if you pass `sensitive_keys`, it's stored as a
    plain attribute on the returned logger (`logger.sensitive_keys`) -
    logger objects are regular Python objects, so attaching an extra
    attribute to one is no different from doing it to any other object.
    This exists purely as a convenience, so a module that owns both a
    logger AND a SENSITIVE_KEYS list (see payments_stripe/const.py,
    payments_adyen/const.py) can call
    `mask_sensitive(data, logger.sensitive_keys)` at its call sites
    without separately importing SENSITIVE_KEYS everywhere it logs
    something. It's entirely optional - `logging.getLogger(name)` plus
    passing your own sensitive_keys list straight to mask_sensitive()
    works exactly as well.
    """
    logger = logging.getLogger(name)
    logger.sensitive_keys = sensitive_keys or []
    return logger


def mask_sensitive(data, sensitive_keys):
    """
    Returns a SHALLOW copy of `data` with any key matching
    `sensitive_keys` (case-insensitive) replaced by the literal string
    "***MASKED***" - every other key is left exactly as-is.

    "Shallow" matters: this only masks keys at the TOP LEVEL of `data`. A
    nested dict value (e.g. `data["stripe_config"]["secret_key"]`) would
    NOT be masked by one call - call this again on that nested dict
    separately if you ever need to mask something nested more than one
    level deep. This project's actual call sites (webhook payloads,
    outbound request/response bodies) only ever need top-level masking in
    practice, so a recursive version wasn't worth the added complexity.

    Not a mutation: `data` itself is never changed - a NEW dict is
    returned, so logging a masked copy can never accidentally persist a
    masked-out value back into whatever `data` came from.

    If `data` isn't a dict at all (e.g. `None`, or some non-dict payload),
    it's returned completely unchanged - there's nothing to mask key-by-
    key in a non-dict value, and returning it as-is (rather than raising)
    means every call site can call mask_sensitive() on "whatever this
    payload happens to be" without checking its type first.
    """
    if not isinstance(data, dict):
        return data

    lowered_sensitive_keys = {key.lower() for key in sensitive_keys}
    return {
        key: "***MASKED***" if key.lower() in lowered_sensitive_keys else value
        for key, value in data.items()
    }
