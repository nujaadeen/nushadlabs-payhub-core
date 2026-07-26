import logging
import re
import uuid

from django.db import models
from django.utils import timezone

from tenants.models import Customer, Tenant

from .exceptions import PaymentProviderRequestError

logger = logging.getLogger(__name__)


def get_adapter_for_provider_code(provider_code):
    """
    Looks up and instantiates the PaymentProviderAdapter subclass matching
    `provider_code`. This is a module-level function (not a method) because
    it's needed from places that don't have a transaction instance to work
    with:
      - PaymentTransaction._get_adapter() (an instance method) - when we
        already have a transaction and want ITS provider's adapter.
      - PaymentTransaction._search_by_reference() (a classmethod) - called
        BEFORE we've found a transaction at all (finding one IS the whole
        point of that method), so there's no `self.provider.code` to read.
      - ProviderListCreateView.get() (payments_core/views.py) - filtering
        GET /providers/ by currency needs to ask each provider's adapter
        what currencies it supports, with only a `code` string on hand.
    Rather than duplicate this if/elif in each place, it lives here once.
    No leading underscore (unlike most of this file's other helpers) -
    since it's called from another module now (views.py), it's part of
    this module's public surface, not a private implementation detail.

    In Odoo, this dispatch happens implicitly via `_inherit`: each provider
    module (payment_stripe, payment_adyen) patches its own version of
    methods like `_get_specific_processing_values` onto payment.transaction,
    and Odoo's ORM automatically calls the right override based on which
    modules are installed. Django has no equivalent to `_inherit` - so we
    reproduce the same "run the right code for this provider" behavior
    ourselves, explicitly. This if/elif IS our whole "override registry" -
    it's the one place that would grow if we added a third provider.

    The adapter class imports below are deliberately INSIDE this function
    (not at the top of this file) to avoid a circular import: this file
    (payments_core/models.py) would import payments_stripe.services, which
    imports payments_stripe.models (for PaymentProviderStripeConfig), which
    itself imports PaymentProvider FROM payments_core.models - i.e. right
    back into the module that's still in the middle of being defined.
    Python can't resolve that at import time. Importing inside the function
    instead delays the import until it actually RUNS (by which point
    payments_core.models has finished loading), which sidesteps the cycle
    entirely.
    """
    if provider_code == "stripe":
        from payments_stripe.services import StripeAdapter

        return StripeAdapter()
    elif provider_code == "adyen":
        from payments_adyen.services import AdyenAdapter

        return AdyenAdapter()

    raise PaymentProviderRequestError(f"No adapter registered for provider code '{provider_code}'.")


class PaymentProvider(models.Model):
    """
    Mirrors Odoo's `payment.provider` model.

    A PaymentProvider is one payment gateway *configuration* for one tenant -
    e.g. "Tenant A's Stripe account" or "Tenant B's Adyen account". The
    provider-specific credentials (API keys etc.) live in a separate model
    in the payments_stripe / payments_adyen apps (PaymentProviderStripeConfig
    / PaymentProviderAdyenConfig), linked back here with a OneToOneField.
    We split it that way so this core model stays adapter-agnostic - it
    doesn't need to know what fields Stripe vs Adyen require.
    """

    # Matches Odoo's payment.provider `state` field exactly: a provider
    # starts disabled, can be flipped to test mode for a developer to try
    # end-to-end without moving real money, and only goes "enabled" once a
    # tenant is ready to take live payments.
    STATE_CHOICES = [
        ("disabled", "Disabled"),
        ("test", "Test Mode"),
        ("enabled", "Enabled"),
    ]

    # Odoo's `_compute_feature_support_fields` reports, per capability, how
    # well a given provider integration supports it: not implemented at all,
    # implemented for none of the flows that would need it, partially
    # implemented, or fully implemented. We mirror those four levels here.
    FEATURE_SUPPORT_CHOICES = [
        ("not_implemented", "Not Implemented"),
        ("none", "None"),
        ("partial", "Partial"),
        ("full", "Full"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="payment_providers")
    # Which adapter this provider row uses, e.g. "stripe" or "adyen". Plain
    # CharField for now - the payments_stripe / payments_adyen apps will
    # constrain this with real choices once they exist as concrete adapters.
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="disabled")

    support_manual_capture = models.CharField(
        max_length=16, choices=FEATURE_SUPPORT_CHOICES, default="not_implemented"
    )
    support_refund = models.CharField(
        max_length=16, choices=FEATURE_SUPPORT_CHOICES, default="not_implemented"
    )
    support_tokenization = models.CharField(
        max_length=16, choices=FEATURE_SUPPORT_CHOICES, default="not_implemented"
    )
    support_express_checkout = models.CharField(
        max_length=16, choices=FEATURE_SUPPORT_CHOICES, default="not_implemented"
    )

    allow_tokenization = models.BooleanField(default=False)
    capture_manually = models.BooleanField(default=False)
    allow_express_checkout = models.BooleanField(default=False)

    # null=True (not just blank=True) because "no minimum/maximum configured"
    # is a real, meaningful state here, distinct from 0.
    minimum_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    maximum_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    is_published = models.BooleanField(default=False)
    is_live = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    # auto_now=True (as opposed to auto_now_add): Django overwrites this
    # field with "now" on EVERY save(), which is exactly what we want for an
    # "updated_at" column.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "code")

    def __str__(self):
        return f"{self.name} ({self.code}) - {self.tenant_id}"

    def _compute_feature_support_fields(self):
        """
        Mirrors Odoo's payment.provider._compute_feature_support_fields().

        In Odoo, this is a @api.depends('code') compute method: whenever a
        provider's `code` changes, Odoo re-derives the four support_* fields
        above by delegating to a code-specific override (e.g. the Stripe
        module overrides this to declare "I support partial refunds").

        This is the BASE implementation - it always declares "nothing is
        implemented", which is correct for a provider with no adapter, and
        is also the correct answer for this whole project right now since we
        have only implemented payment creation so far (no capture, refund,
        tokenization, or express checkout for any provider yet).

        We don't have Odoo's compute-field framework in Django (no
        @api.depends, no real inheritance between this model and the
        Stripe/Adyen adapter apps), so instead each adapter app exposes its
        own get_feature_support_fields(provider) function - see
        payments_stripe/services.py and payments_adyen/services.py - which
        calls this base method and then overrides individual keys as real
        support gets implemented. That function call is the Django
        equivalent of Odoo's override chain here.
        """
        return {
            "support_manual_capture": "not_implemented",
            "support_refund": "not_implemented",
            "support_tokenization": "not_implemented",
            "support_express_checkout": "not_implemented",
        }

    def _ensure_provider_is_not_disabled(self):
        """
        Mirrors Odoo's payment.provider._ensure_provider_is_not_disabled().

        A "disabled" provider is one a tenant has configured but switched
        off - it shouldn't be usable to create new payments, even though the
        row (and its credentials) still exist. Odoo raises a UserError here,
        which its web layer knows how to turn into a friendly error page; we
        don't have that machinery, so we raise a plain ValueError instead
        and let the calling view (POST /payments/) catch it and turn it into
        a 400 response.
        """
        if self.state == "disabled":
            raise ValueError(f"Provider '{self.name}' is disabled and cannot process payments.")


class PaymentToken(models.Model):
    """
    Mirrors Odoo's `payment.token` model.

    A PaymentToken represents a stored, reusable payment method for a
    customer (e.g. a saved credit card) so they don't have to re-enter their
    card details on every purchase. The actual vault reference (whatever
    Stripe/Adyen gives us back to represent "this saved card") lives in the
    adapter-specific sibling model (PaymentTokenStripe / PaymentTokenAdyen),
    linked back here with a OneToOneField - same split rationale as
    PaymentProvider above.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="payment_tokens")
    # on_delete=PROTECT (not CASCADE): we never hard-delete a PaymentProvider
    # (see the DELETE /providers/{id}/ endpoint - it soft-deletes by flipping
    # state/is_published instead). PROTECT is a second line of defense: if
    # something ever DID try to hard-delete a provider row directly, Django
    # would refuse and raise ProtectedError rather than silently wiping out
    # every saved card that points at it.
    provider = models.ForeignKey(
        PaymentProvider, on_delete=models.PROTECT, related_name="tokens"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="payment_tokens"
    )
    # A masked, display-safe representation of the underlying payment method,
    # e.g. "Visa ...4242". Never the real card/account number.
    payment_details = models.CharField(max_length=255, null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.payment_details or str(self.id)


class PaymentTransaction(models.Model):
    """
    Mirrors Odoo's `payment.transaction` model.

    A PaymentTransaction is a single attempt to move money: a payment, a
    refund, or a capture of a previously authorized payment. Refunds and
    captures are represented as their own PaymentTransaction rows that point
    back at the original transaction via `source_transaction` (see below),
    exactly like Odoo's `source_transaction_id`.
    """

    STATE_CHOICES = [
        ("draft", "Draft"),
        ("pending", "Pending"),
        ("authorized", "Authorized"),
        ("done", "Done"),
        ("cancel", "Cancelled"),
        ("error", "Error"),
    ]

    OPERATION_CHOICES = [
        ("online_redirect", "Online Redirect"),
        ("online_direct", "Online Direct"),
        ("online_token", "Online Token"),
        ("refund", "Refund"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="transactions")
    # on_delete=PROTECT - same reasoning as PaymentToken.provider above: a
    # provider is soft-deleted (see DELETE /providers/{id}/), never hard-
    # deleted, and this FK guarantees the database itself will refuse a hard
    # delete that would otherwise orphan (or silently wipe out) real
    # payment history.
    provider = models.ForeignKey(
        PaymentProvider, on_delete=models.PROTECT, related_name="transactions"
    )
    token = models.ForeignKey(
        PaymentToken, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions"
    )
    # Self-referencing FK: a refund or capture transaction points back at the
    # original transaction it was spawned from. `null=True` because most
    # transactions (the original payment itself) have no source - only child
    # transactions (refunds/captures) do.
    source_transaction = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="child_transactions"
    )

    # Our own reference for this transaction, generated by _compute_reference
    # (Phase 2). Unique per tenant so two tenants can't collide, but each
    # tenant's own reference sequence is guaranteed unique.
    reference = models.CharField(max_length=255)
    # The identifier the payment provider (Stripe/Adyen) assigns to this
    # transaction on their side, once we've sent it to them. Null until then.
    provider_reference = models.CharField(max_length=255, null=True, blank=True)

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="transactions")
    # ISO 4217 currency code, e.g. "USD", "EUR" - always 3 letters.
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    captured_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="draft")
    state_message = models.TextField(null=True, blank=True)
    operation = models.CharField(max_length=32, choices=OPERATION_CHOICES, default="online_redirect")
    last_state_change = models.DateTimeField(null=True, blank=True)

    tokenize = models.BooleanField(default=False)
    is_express_checkout = models.BooleanField(default=False)
    return_url = models.CharField(max_length=255, null=True, blank=True)
    is_live = models.BooleanField(default=False)
    is_post_processed = models.BooleanField(default=False)

    # Odoo spreads provider-specific response data across many dedicated
    # columns (e.g. stripe_payment_intent, adyen_psp_reference, ...). We
    # deliberately avoid that column sprawl and instead store the raw
    # provider payload here as JSON. Postgres' JSONField lets us query into
    # this later if needed, without a migration every time a new provider
    # adds a new field to its response payload.
    provider_data = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "reference")

    def __str__(self):
        return f"{self.reference} [{self.state}]"

    # ------------------------------------------------------------------
    # Method stubs below mirror Odoo's payment.transaction method names.
    # None of them have real logic yet - this is Phase 0 scaffolding only.
    # Business logic is implemented in Phase 2/3.
    # ------------------------------------------------------------------

    @classmethod
    def _compute_reference(cls, provider_code, tenant_id, prefix=None, separator="-", **kwargs):
        """
        Mirrors Odoo's payment.transaction._compute_reference().

        `provider_code` is kept in the signature to match Odoo's method
        shape (and because a future phase might want it to influence the
        prefix), but this simplified version doesn't actually use it -
        we skip Odoo's `_compute_reference_prefix` hook system entirely and
        just fall back to a plain timestamp-based prefix. That's overkill
        for a learning project with one reference-generation strategy.

        Odoo's uniqueness check is GLOBAL - one payment.transaction table
        shared by the whole Odoo database. We're multi-tenant with every
        tenant's transactions in that same shared table, so a global
        uniqueness check would be wrong here: two different tenants
        creating a transaction in the same second could easily generate the
        same "tx-<timestamp>" prefix, even though they have nothing to do
        with each other and neither cares about the other's references. We
        scope the uniqueness check to `tenant_id` instead - this is our
        adaptation of Odoo's approach, not a straight copy.
        """
        if not prefix:
            # Odoo's own fallback prefix is built from the record's own id
            # once it exists; a UUID pk (see PaymentTransaction.id above)
            # doesn't give us a nice short number to use the same way, so we
            # use a plain timestamp instead - simple, and unique enough for
            # this project's purposes.
            prefix = f"tx-{int(timezone.now().timestamp())}"

        exact_match_exists = cls.objects.filter(tenant_id=tenant_id, reference=prefix).exists()
        if not exact_match_exists:
            # Nobody's using this bare prefix yet for this tenant - no
            # suffix needed, exactly like Odoo's first-use case.
            return prefix

        # The bare prefix is already taken for this tenant - we need a
        # "{prefix}{separator}{n}" suffix instead. We can't just count how
        # many rows start with the prefix and add one - if a transaction
        # was ever deleted, that count would be too low and we'd generate a
        # reference that collides with one still in use. Instead, like
        # Odoo, we use a regex to find the ACTUAL highest suffix number
        # currently in use among this tenant's references, and pick one
        # higher than that - this is correct no matter what gaps exist.
        candidates = cls.objects.filter(
            tenant_id=tenant_id, reference__startswith=f"{prefix}{separator}"
        )
        suffix_pattern = re.compile(rf"^{re.escape(prefix)}{re.escape(separator)}(\d+)$")
        max_suffix = 0
        for candidate in candidates:
            match = suffix_pattern.match(candidate.reference)
            if match:
                max_suffix = max(max_suffix, int(match.group(1)))

        return f"{prefix}{separator}{max_suffix + 1}"

    def _get_processing_values(self):
        """
        Mirrors Odoo's payment.transaction._get_processing_values().

        Builds the dict of values the API caller (and, internally, the
        chosen adapter) needs to actually kick off a payment: our own
        reference/amount/currency plus whatever provider-specific values
        `_get_specific_processing_values` below returns (for this phase,
        that includes making the real Stripe/Adyen API call and returning
        its `redirect_url`).
        """
        processing_values = {
            "provider_id": self.provider_id,
            "provider_code": self.provider.code,
            "reference": self.reference,
            "amount": self.amount,
            "currency": self.currency,
            "customer_id": self.customer_id,
            # TODO: Phase 3 - tokenization isn't implemented yet (see the
            # _tokenize/_extract_token_values stubs below), so we never ask
            # a provider to save the payment method for reuse. Always False
            # until that phase exists.
            "tokenize": False,
        }
        processing_values.update(self._get_specific_processing_values())
        return processing_values

    def _get_adapter(self):
        """
        Instance-method convenience wrapper around the module-level
        get_adapter_for_provider_code() (see its docstring near the top of
        this file for the full explanation of this dispatch pattern) -
        reads the provider code off THIS transaction's own provider.
        """
        return get_adapter_for_provider_code(self.provider.code)

    def _get_specific_processing_values(self):
        """
        Mirrors Odoo's payment.transaction._get_specific_processing_values().

        See _get_adapter() above for the full explanation of how - and why
        - this project picks the right adapter without Odoo's `_inherit`
        mechanism.
        """
        return self._get_adapter().get_specific_processing_values(self)

    @classmethod
    def _process(cls, provider_code, payment_data):
        """
        Mirrors Odoo's payment.transaction._process() - the central entry
        point every asynchronous notification (a webhook, or the customer
        returning from a redirect) funnels through to actually update a
        transaction's state. This is the single most important method in
        the whole payment pipeline: everything upstream (creating the
        transaction, sending the payment request) exists to set up a
        transaction that CAN be found and processed here; everything
        downstream (webhooks, return URLs - see payments_core/views.py)
        exists to CALL this, with whatever payload shape their provider
        gave them.

        Mirrors Odoo's version in shape, with two pieces intentionally left
        out:
          - Odoo's version also calls `tx.tokenize(...)` when `tx.tokenize`
            is True, to save the payment method for reuse. We don't - that
            stays deferred to a later phase (see the _tokenize /
            _extract_token_values stubs below), and this project's
            PaymentTransaction.tokenize is always False anyway (see
            _get_processing_values' TODO comment above).
          - Odoo also sends a real-time notification over its "bus" (a
            websocket-based pub/sub system) so an open browser tab showing
            a payment status page updates itself live. We're a pure JSON
            API with no frontend to notify - GET /payments/{id}/ is polled
            instead - so there's nothing to send here.
        """
        tx = cls._search_by_reference(provider_code, payment_data)
        if tx is None:
            logger.warning(
                "PaymentTransaction._process: no transaction found for "
                "provider_code=%s, payment_data=%s",
                provider_code,
                payment_data,
            )
            return None

        tx._apply_updates(payment_data)

        if tx.state in ("authorized", "done"):
            # Only bother validating the amount once the provider claims
            # the payment actually went through (fully or provisionally) -
            # no point checking amounts on a transaction that's still
            # pending or already failed.
            tx._validate_amount(payment_data)

        # A plain, full save() (no update_fields) - deliberately, unlike
        # _update_state's targeted save above. _apply_updates() may have
        # set attributes (like provider_reference) directly on `tx` without
        # saving them itself (its state-transition call, if any, only
        # persists the state-related fields - see _update_state), so this
        # final save is what actually flushes whatever DID change, however
        # many or few fields that turned out to be.
        tx.save()
        return tx

    @classmethod
    def _search_by_reference(cls, provider_code, payment_data):
        """
        Mirrors Odoo's payment.transaction._search_by_reference().

        Dispatches to the adapter matching `provider_code` (via the
        module-level get_adapter_for_provider_code() near the top of this
        file - NOT the instance method _get_adapter(), since this is a
        classmethod: there's no transaction instance yet to read a provider
        code off of - finding one IS the whole point of this method). Each
        adapter knows where in ITS OWN payload shape the reference actually
        lives (see StripeAdapter.search_by_reference /
        AdyenAdapter.search_by_reference).
        """
        try:
            adapter = get_adapter_for_provider_code(provider_code)
        except PaymentProviderRequestError:
            logger.warning(
                "_search_by_reference: no adapter registered for provider_code '%s'.",
                provider_code,
            )
            return None

        return adapter.search_by_reference(payment_data)

    def _apply_updates(self, payment_data):
        """
        Mirrors Odoo's payment.transaction._apply_updates().

        Takes the provider's payment data and updates this transaction's
        state accordingly (e.g. moves it from draft -> done), by dispatching
        to the adapter matching this transaction's provider (same
        _get_adapter() lookup _get_specific_processing_values uses above) -
        each adapter knows how to read its own provider's payload shape.

        Called from three different places, all funneling through
        _process() above: Adyen's synchronous /payments response (when it
        completes immediately - see AdyenAdapter.get_specific_processing_values),
        and - as of this phase - both providers' webhooks and Stripe's/
        Adyen's return-URL flows (see payments_core/views.py). This method
        itself doesn't need to know or care which of those triggered it.
        """
        self._get_adapter().apply_updates(self, payment_data)

    def _validate_amount(self, payment_data):
        """
        Mirrors Odoo's payment.transaction._validate_amount().

        Confirms the amount/currency the provider says was charged actually
        matches what we expected to charge (self.amount/self.currency),
        guarding against a tampered notification claiming a smaller amount
        was paid than we asked for. Called from _process() above, only once
        a transaction has reached "authorized" or "done" - no point
        checking amounts on a transaction that's still pending or has
        already failed for an unrelated reason.
        """
        amount_data = self._extract_amount_data(payment_data)
        extracted_amount = amount_data.get("amount")
        extracted_currency = amount_data.get("currency")

        if extracted_amount is None or extracted_currency is None:
            # This payload didn't actually contain an amount/currency we
            # could extract (e.g. a webhook event type that doesn't carry
            # one) - nothing to compare, so there's nothing to flag.
            return

        if extracted_amount != self.amount or extracted_currency != self.currency:
            # _set_error can be called from "authorized"/"done" specifically
            # BECAUSE of this case - see _set_error's docstring below for
            # why those two states had to be added to its allowed source
            # states in this phase.
            self._set_error(
                f"Amount or currency mismatch: expected {self.amount} {self.currency}, "
                f"provider reported {extracted_amount} {extracted_currency}."
            )

    def _extract_amount_data(self, payment_data):
        """
        Mirrors Odoo's payment.transaction._extract_amount_data().

        Dispatches to the adapter (same _get_adapter() lookup as
        _get_specific_processing_values/_apply_updates above) - each
        adapter knows where in its own payload shape the amount/currency
        actually live, and returns them already converted to our
        major-unit Decimal representation (see to_major_currency_units in
        payments_core/utils.py).
        """
        return self._get_adapter().extract_amount_data(self, payment_data)

    def _tokenize(self, payment_data):
        """
        Mirrors Odoo's payment.transaction._tokenize().

        Creates a PaymentToken from this transaction's payment data so the
        customer's payment method can be reused later. Implemented in
        Phase 3.
        """
        raise NotImplementedError("Implemented in Phase 3")

    def _extract_token_values(self, payment_data):
        """
        Mirrors Odoo's payment.transaction._extract_token_values().

        Pulls the adapter-specific fields needed to build a
        PaymentTokenStripe/PaymentTokenAdyen row out of a provider payload.
        Implemented in Phase 3.
        """
        raise NotImplementedError("Implemented in Phase 3")

    def _update_state(self, allowed_states, target_state, state_message=None):
        """
        Mirrors Odoo's payment.transaction._update_state().

        Odoo's version runs over a whole recordset at once: it splits the
        records into "in an allowed source state" (which get updated) and
        "not" (which get skipped, with a logged warning) - because a single
        Odoo call can be asked to update many transactions together. We only
        ever operate on one transaction instance at a time in this project,
        so this is the same guard logic simplified down to a single record:
        if `self.state` isn't one of the allowed source states for this
        transition, we refuse it outright (raise) instead of silently
        skipping it - there's no batch of "other records" to fall back to
        updating, so silently doing nothing would just hide a bug.
        """
        if self.state not in allowed_states:
            raise ValueError(
                f"Cannot move transaction {self.reference} from state "
                f"'{self.state}' to '{target_state}' - allowed source "
                f"states for this transition are {allowed_states}."
            )

        self.state = target_state
        self.state_message = state_message
        # timezone.now() (not datetime.now()) because this project runs with
        # USE_TZ=True (see settings.py) - Django expects/produces
        # timezone-AWARE datetimes everywhere when that's on, and
        # datetime.now() would give us a naive one, which triggers a
        # RuntimeWarning and can compare incorrectly against aware datetimes
        # elsewhere.
        self.last_state_change = timezone.now()
        self.save(update_fields=["state", "state_message", "last_state_change", "updated_at"])

    def _set_pending(self, state_message=None):
        """
        Mirrors Odoo's payment.transaction._set_pending().

        Allowed source state: 'draft'. A transaction can only become
        "pending" (waiting on the customer to finish paying, or on the
        provider to notify us of the result) right after being created -
        this project only ever calls this once, right after a successful
        POST /payments/ adapter call.
        """
        self._update_state(
            allowed_states=("draft",), target_state="pending", state_message=state_message
        )

    def _set_authorized(self, state_message=None):
        """
        Mirrors Odoo's payment.transaction._set_authorized().

        Allowed source states: 'draft', 'pending' - matches Odoo exactly.
        "Authorized" means the provider has reserved/approved the payment
        but the money hasn't actually moved yet - that only happens on a
        manual capture (payment.provider.capture_manually /
        PaymentProvider.capture_manually). We don't implement sending a
        capture request yet (see StripeAdapter/AdyenAdapter.send_capture_request,
        still Phase 3+ stubs), so nothing in THIS project currently drives a
        transaction to "authorized" - but the state has to exist and be
        reachable now so the state machine's shape is already correct for
        when capture support is added, rather than needing a state-machine
        change alongside a capture-flow change later.
        """
        self._update_state(
            allowed_states=("draft", "pending"), target_state="authorized", state_message=state_message
        )

    def _set_done(self, state_message=None):
        """
        Mirrors Odoo's payment.transaction._set_done().

        Allowed source states: 'draft', 'pending', 'authorized' - matches
        Odoo (which also allows 'error', for a transaction that failed and
        was later corrected; we don't have a path that re-drives an
        'error' transaction back to 'done' in this project, so we leave
        that one out). 'authorized' had to be added in this phase: once
        _set_authorized (above) makes "authorized" reachable, a manual
        capture completing would move a transaction from "authorized"
        straight to "done" - we don't implement sending capture requests
        yet either, but again, the state machine's SHAPE should already be
        correct.

        'draft' stays in the allowed states too because of Adyen's
        immediate-response case (see AdyenAdapter.get_specific_processing_values
        in payments_adyen/services.py) - the one case in this project where
        a transaction reaches "done" WITHOUT ever passing through "pending"
        first.
        """
        self._update_state(
            allowed_states=("draft", "pending", "authorized"),
            target_state="done",
            state_message=state_message,
        )

    def _set_canceled(self, state_message=None):
        """
        Mirrors Odoo's payment.transaction._set_canceled().

        Allowed source states: 'draft', 'pending', 'authorized' - matches
        Odoo exactly. A transaction can be canceled by the customer
        abandoning a redirect/3DS challenge (still 'pending'), or by the
        provider canceling an authorized-but-not-yet-captured payment.
        """
        self._update_state(
            allowed_states=("draft", "pending", "authorized"),
            target_state="cancel",
            state_message=state_message,
        )

    def _set_error(self, state_message):
        """
        Mirrors Odoo's payment.transaction._set_error().

        Allowed source states: 'draft', 'pending', 'authorized', 'done'.
        Phase 2 only ever called this right after a failed adapter call (so
        only 'draft'/'pending' were reachable then). This phase adds
        'authorized' and 'done' too, specifically because of
        _validate_amount above: if a provider reports a DIFFERENT amount
        than we expected, we need to be able to flag that as an error even
        AFTER the transaction has already reached "authorized" or "done" -
        an amount mismatch on an already-"done" transaction is exactly the
        kind of thing 'error' needs to be reachable from, since it might
        indicate the notification was tampered with. We still don't allow
        'cancel' -> 'error' or 'error' -> 'error', since nothing in this
        project needs those transitions.
        """
        self._update_state(
            allowed_states=("draft", "pending", "authorized", "done"),
            target_state="error",
            state_message=state_message,
        )

    def _create_child_transaction(self, amount, is_refund=False):
        """
        Mirrors Odoo's payment.transaction._create_child_transaction().

        Creates a new PaymentTransaction row linked to this one via
        `source_transaction`, representing a partial/full capture or refund
        of this transaction. Implemented in Phase 3.
        """
        raise NotImplementedError("Implemented in Phase 3")
