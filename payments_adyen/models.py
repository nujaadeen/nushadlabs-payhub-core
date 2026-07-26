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
    # Required for the Hosted Checkout flow this project uses (see
    # AdyenAdapter.get_specific_processing_values in payments_adyen/
    # services.py for the full explanation of why we use Hosted Checkout at
    # all). Unlike the other fields above, this ISN'T a secret credential -
    # it's an id Adyen assigns when a merchant creates a "theme" (page
    # branding/layout) in their Adyen Customer Area under Pay by Link >
    # Themes. There is no API to create one; it's a one-time manual setup
    # step per Adyen merchant account, confirmed against Adyen's current
    # Hosted Checkout integration docs while building this. null=True/
    # blank=True because a provider row can exist before this manual step
    # has been done - get_specific_processing_values raises a clear error
    # if a payment is actually attempted without it, rather than failing
    # confusingly inside the Adyen API call.
    adyen_theme_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        # Without this, Django would name the table
        # "payments_adyen_paymentprovideradyenconfig" (the word "adyen"
        # appearing twice, "provider"+"adyen"+"config" all run together).
        # We override it with `db_table` to get a clean snake_case name
        # matching our original schema design.
        db_table = "payment_provider_adyen_config"

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

    class Meta:
        # Without this, Django would name the table
        # "payments_adyen_paymenttokenadyen". We override it with
        # `db_table` to get a clean snake_case name matching our original
        # schema design.
        db_table = "payment_token_adyen"

    def __str__(self):
        return f"Adyen details for token {self.token_id}"
