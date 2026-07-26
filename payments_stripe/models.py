from django.db import models

from payments_core.models import PaymentProvider, PaymentToken


class PaymentProviderStripeConfig(models.Model):
    """
    Stripe-specific credentials for one PaymentProvider row.

    Split out from PaymentProvider (payments_core) via a OneToOneField so the
    core model never has to know about Stripe-specific fields - only the
    payments_stripe app does. This is the same "adapter owns its own fields"
    pattern as PaymentTokenStripe below.
    """

    provider = models.OneToOneField(
        PaymentProvider, on_delete=models.CASCADE, related_name="stripe_config"
    )
    # SECURITY NOTE: these credential fields are stored raw (plain text) for
    # now - this is a deliberate, temporary decision for this project phase,
    # not an oversight. Encryption at rest will be added later.
    stripe_publishable_key = models.CharField(max_length=255)  # stored raw for now, encrypt later
    stripe_secret_key = models.CharField(max_length=255)  # stored raw for now, encrypt later
    stripe_webhook_secret = models.CharField(max_length=255)  # stored raw for now, encrypt later

    def __str__(self):
        return f"Stripe config for {self.provider_id}"


class PaymentTokenStripe(models.Model):
    """
    Stripe-specific fields for one PaymentToken row (payments_core).

    Mirrors the stripe_payment_method / stripe_mandate columns Odoo's
    payment_token table carries for Stripe-backed tokens.
    """

    token = models.OneToOneField(
        PaymentToken, on_delete=models.CASCADE, related_name="stripe_details"
    )
    # Stripe's identifier for the saved payment method (e.g. "pm_...").
    stripe_payment_method = models.CharField(max_length=255, null=True, blank=True)
    # Stripe's identifier for the mandate authorizing future off-session
    # charges against this payment method (e.g. required for SEPA).
    stripe_mandate = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Stripe details for token {self.token_id}"
