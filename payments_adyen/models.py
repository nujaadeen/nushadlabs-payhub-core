from django.db import models

from payments_core.models import PaymentProvider, PaymentToken


class PaymentProviderAdyenConfig(models.Model):
    """
    Adyen-specific credentials for one PaymentProvider row.

    Same split rationale as PaymentProviderStripeConfig in payments_stripe:
    keeps Adyen-specific fields out of the shared payments_core model.
    """

    provider = models.OneToOneField(
        PaymentProvider, on_delete=models.CASCADE, related_name="adyen_config"
    )
    # SECURITY NOTE: these credential fields are stored raw (plain text) for
    # now - this is a deliberate, temporary decision for this project phase,
    # not an oversight. Encryption at rest will be added later.
    adyen_merchant_account = models.CharField(max_length=255)  # stored raw for now, encrypt later
    adyen_api_key = models.CharField(max_length=255)  # stored raw for now, encrypt later
    adyen_client_key = models.CharField(max_length=255)  # stored raw for now, encrypt later
    adyen_hmac_key = models.CharField(max_length=255)  # stored raw for now, encrypt later

    def __str__(self):
        return f"Adyen config for {self.provider_id}"


class PaymentTokenAdyen(models.Model):
    """
    Adyen-specific fields for one PaymentToken row (payments_core).

    Mirrors the recurring_detail_reference / adyen_shopper_reference columns
    Odoo's payment_token table carries for Adyen-backed tokens.
    """

    token = models.OneToOneField(
        PaymentToken, on_delete=models.CASCADE, related_name="adyen_details"
    )
    # Adyen's identifier for the stored recurring payment detail (saved card).
    recurring_detail_reference = models.CharField(max_length=255, null=True, blank=True)
    # Adyen's identifier for the shopper this token belongs to on their side.
    adyen_shopper_reference = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Adyen details for token {self.token_id}"
