from django.db import transaction
from rest_framework import serializers

from payments_adyen import services as adyen_services
from payments_adyen.models import PaymentProviderAdyenConfig
from payments_stripe import services as stripe_services
from payments_stripe.models import PaymentProviderStripeConfig

from .models import PaymentProvider, PaymentTransaction

# The request body uses short, generic key names (e.g. "publishable_key")
# but the actual database columns are prefixed per-adapter (e.g.
# "stripe_publishable_key") so that PaymentProviderStripeConfig and
# PaymentProviderAdyenConfig columns don't collide if ever queried together.
# These maps translate between the two - "request key" -> "model field name".
STRIPE_CONFIG_FIELD_MAP = {
    "publishable_key": "stripe_publishable_key",
    "secret_key": "stripe_secret_key",
    "webhook_secret": "stripe_webhook_secret",
}
ADYEN_CONFIG_FIELD_MAP = {
    "merchant_account": "adyen_merchant_account",
    "api_key": "adyen_api_key",
    "client_key": "adyen_client_key",
    "hmac_key": "adyen_hmac_key",
    "theme_id": "adyen_theme_id",
}


class StripeConfigInputSerializer(serializers.Serializer):
    """
    Plain DRF `Serializer` (NOT `ModelSerializer`) for the nested
    "stripe_config" input on create/update. We use a plain Serializer here
    because the request field names (publishable_key, ...) don't match the
    PaymentProviderStripeConfig column names (stripe_publishable_key, ...) -
    a ModelSerializer would try to map fields 1:1 to the model by name and
    fail, so we describe the input shape by hand instead and translate it
    ourselves (see STRIPE_CONFIG_FIELD_MAP above).
    """

    publishable_key = serializers.CharField()
    secret_key = serializers.CharField()
    webhook_secret = serializers.CharField()


class AdyenConfigInputSerializer(serializers.Serializer):
    """Same rationale as StripeConfigInputSerializer, for Adyen's fields."""

    merchant_account = serializers.CharField()
    api_key = serializers.CharField()
    client_key = serializers.CharField()
    hmac_key = serializers.CharField()
    # Optional (matches adyen_theme_id's null=True/blank=True on
    # PaymentProviderAdyenConfig - see that field's comment in
    # payments_adyen/models.py for what this is and why it's not always
    # available yet at provider-creation time). Payments will fail with a
    # clear error at POST /payments/ time if this is still missing when a
    # payment is actually attempted - see AdyenAdapter.
    # get_specific_processing_values in payments_adyen/services.py.
    theme_id = serializers.CharField(required=False, allow_blank=True)


class PaymentProviderReadSerializer(serializers.ModelSerializer):
    """
    Used for every response that returns provider data: GET /providers/,
    GET /providers/{id}/, and the response body of POST/PATCH.

    Deliberately excludes stripe_config/adyen_config (the credential rows)
    entirely - there's no field on this serializer for them at all, so DRF
    has no way to render them even by accident. We're not encrypting
    credentials yet (see payments_stripe/payments_adyen models), so keeping
    them out of every response body is a basic API hygiene habit, not a
    real security control - anyone with direct database access can still
    read them.
    """

    # Django gives every ForeignKey field an automatic "<field>_id"
    # attribute alongside the "<field>" attribute - e.g. `provider.tenant`
    # (a full Tenant object, costs a DB query to load) and `provider.tenant_id`
    # (just the raw UUID already sitting on this row, no extra query).
    # Naming this serializer field "tenant_id" makes DRF read straight from
    # that attribute by default (no `source=` needed - DRF only needs
    # `source=` when the serializer field name DOESN'T match the model
    # attribute name), so the API response includes the tenant's id without
    # Django ever fetching the related Tenant row.
    tenant_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = PaymentProvider
        fields = [
            "id",
            "tenant_id",
            "code",
            "name",
            "state",
            "support_manual_capture",
            "support_refund",
            "support_tokenization",
            "support_express_checkout",
            "allow_tokenization",
            "capture_manually",
            "allow_express_checkout",
            "minimum_amount",
            "maximum_amount",
            "is_published",
            "is_live",
            "created_at",
            "updated_at",
        ]
        # Every field here is output-only for this serializer - creating and
        # updating providers is handled by the two serializers below, which
        # each expose only the specific fields their endpoint is allowed to
        # write. "tenant_id" is excluded from this list because it's already
        # explicitly declared as read_only=True above - DRF raises an error
        # if a field is both explicitly declared AND listed here.
        read_only_fields = [f for f in fields if f != "tenant_id"]


class PaymentProviderCreateSerializer(serializers.ModelSerializer):
    """
    Handles POST /providers/.

    DRF beginner trip-up this class works around: declaring a nested
    serializer field (stripe_config / adyen_config below) only tells DRF
    the *shape* of the nested input, for validation purposes. It does NOT
    teach DRF how to save that nested data anywhere - ModelSerializer's
    default .create() only knows how to insert one row into one table (the
    table named in Meta.model). Since our nested data belongs in a
    *different* model, in a *different app* (PaymentProviderStripeConfig /
    PaymentProviderAdyenConfig), we have to override .create() ourselves
    and do both inserts by hand. That's what happens below.
    """

    tenant_id = serializers.UUIDField()
    stripe_config = StripeConfigInputSerializer(required=False)
    adyen_config = AdyenConfigInputSerializer(required=False)

    class Meta:
        model = PaymentProvider
        fields = ["tenant_id", "code", "name", "stripe_config", "adyen_config"]

    def validate_code(self, value):
        if value not in ("stripe", "adyen"):
            raise serializers.ValidationError('code must be either "stripe" or "adyen".')
        return value

    def validate(self, attrs):
        code = attrs.get("code")
        if code == "stripe" and "stripe_config" not in attrs:
            raise serializers.ValidationError(
                {"stripe_config": "This field is required when code is 'stripe'."}
            )
        if code == "adyen" and "adyen_config" not in attrs:
            raise serializers.ValidationError(
                {"adyen_config": "This field is required when code is 'adyen'."}
            )
        return attrs

    def create(self, validated_data):
        stripe_config_data = validated_data.pop("stripe_config", None)
        adyen_config_data = validated_data.pop("adyen_config", None)
        tenant_id = validated_data.pop("tenant_id")

        # transaction.atomic() groups every database write inside this
        # `with` block into a single, all-or-nothing unit: either every
        # row created below (the PaymentProvider row AND its
        # StripeConfig/AdyenConfig credential row) gets committed together,
        # or - if anything raises partway through - the database rolls ALL
        # of it back, as if none of it had ever happened. Without this,
        # a failure right after creating the PaymentProvider but before
        # creating its credential row would leave a useless, credential-less
        # provider sitting in the database.
        with transaction.atomic():
            provider = PaymentProvider.objects.create(tenant_id=tenant_id, **validated_data)

            if provider.code == "stripe":
                PaymentProviderStripeConfig.objects.create(
                    provider=provider,
                    **{
                        STRIPE_CONFIG_FIELD_MAP[key]: value
                        for key, value in stripe_config_data.items()
                    },
                )
                support_fields = stripe_services.get_feature_support_fields(provider)
            else:
                # validate() above guarantees code is "adyen" here, since
                # it's already confirmed to be "stripe" or "adyen".
                PaymentProviderAdyenConfig.objects.create(
                    provider=provider,
                    **{
                        ADYEN_CONFIG_FIELD_MAP[key]: value
                        for key, value in adyen_config_data.items()
                    },
                )
                support_fields = adyen_services.get_feature_support_fields(provider)

            # Odoo recomputes the support_* fields via a compute field that
            # fires automatically whenever `code` changes. We don't use a
            # Django signal (e.g. post_save) to reproduce that here on
            # purpose: a signal would run this logic invisibly, from
            # anywhere, any time a PaymentProvider is saved - powerful, but
            # hard to trace when you're still learning how the framework
            # fits together. Calling it directly, right here, keeps the
            # entire "what happens when a provider is created" story in one
            # place, readable top to bottom.
            for field_name, value in support_fields.items():
                setattr(provider, field_name, value)
            provider.save()

        return provider


class PaymentProviderUpdateSerializer(serializers.ModelSerializer):
    """
    Handles PATCH /providers/{id}/ (always partial - see the view, which
    instantiates this with partial=True).
    """

    stripe_config = StripeConfigInputSerializer(required=False)
    adyen_config = AdyenConfigInputSerializer(required=False)

    class Meta:
        model = PaymentProvider
        fields = [
            "name",
            "state",
            "minimum_amount",
            "maximum_amount",
            "is_published",
            "allow_tokenization",
            "capture_manually",
            "allow_express_checkout",
            "stripe_config",
            "adyen_config",
        ]

    def validate(self, attrs):
        # self.instance is set by the view: PaymentProviderUpdateSerializer(
        # provider, data=..., partial=True) - `provider` becomes self.instance.
        instance = self.instance
        if "stripe_config" in attrs and instance.code != "stripe":
            raise serializers.ValidationError(
                {"stripe_config": "This provider is not a stripe provider."}
            )
        if "adyen_config" in attrs and instance.code != "adyen":
            raise serializers.ValidationError(
                {"adyen_config": "This provider is not an adyen provider."}
            )
        return attrs

    def update(self, instance, validated_data):
        stripe_config_data = validated_data.pop("stripe_config", None)
        adyen_config_data = validated_data.pop("adyen_config", None)

        if "state" in validated_data and validated_data["state"] != instance.state:
            # Odoo's payment.provider.write() override runs side effects
            # when `state` changes (e.g. archiving saved payment tokens when
            # a provider is disabled). We are NOT implementing that
            # side-effect logic yet - this PATCH endpoint only updates the
            # plain field itself.
            # TODO: mirror Odoo's state-change side effects in a later phase.
            pass

        with transaction.atomic():
            for field_name, value in validated_data.items():
                setattr(instance, field_name, value)
            instance.save()

            if stripe_config_data:
                # `instance.stripe_config` works because of the
                # related_name="stripe_config" on PaymentProviderStripeConfig
                # .provider (payments_stripe/models.py) - Django uses that
                # related_name to build this reverse accessor automatically.
                config = instance.stripe_config
                for key, value in stripe_config_data.items():
                    setattr(config, STRIPE_CONFIG_FIELD_MAP[key], value)
                config.save()

            if adyen_config_data:
                config = instance.adyen_config
                for key, value in adyen_config_data.items():
                    setattr(config, ADYEN_CONFIG_FIELD_MAP[key], value)
                config.save()

        return instance


class PaymentTransactionCreateSerializer(serializers.Serializer):
    """
    Handles the INPUT side of POST /payments/.

    Plain DRF `Serializer` (not `ModelSerializer`) on purpose: the actual
    row-creation-then-external-call-then-update flow for this endpoint is
    deliberately spread across several explicit steps in the view (see
    PaymentListCreateView.post() in views.py) rather than being
    collapsed into a single serializer.save() the way our provider
    serializers do it - because, unlike a provider, creating a
    PaymentTransaction here involves an external HTTP call to Stripe/Adyen
    in between two separate database writes, which shouldn't be hidden
    inside one .save() call (see that view for the full explanation of why).
    This serializer's only job is validating the shape of the input.
    """

    tenant_id = serializers.UUIDField()
    provider_id = serializers.UUIDField()
    customer_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    # min_length=3 as well as max_length=3: without min_length, "US" (2
    # chars) would pass validation here and only fail later, confusingly,
    # wherever the currency code actually gets used.
    currency = serializers.CharField(min_length=3, max_length=3)
    return_url = serializers.CharField()


class PaymentTransactionReadSerializer(serializers.ModelSerializer):
    """
    Handles the OUTPUT side of GET /payments/{id}/ - the polling endpoint an
    API caller uses to check a payment's current status. Deliberately a
    small field list: just enough for a caller to know what's going on with
    their payment, nothing internal (no tenant/provider/customer FKs, no
    provider_data raw payload).
    """

    class Meta:
        model = PaymentTransaction
        fields = [
            "id",
            "reference",
            "state",
            "amount",
            "currency",
            "provider_reference",
            "state_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PaymentTransactionProviderSerializer(serializers.ModelSerializer):
    """
    A tiny nested representation of a transaction's provider, used only by
    PaymentTransactionListSerializer below - just enough (code + name) to
    show something human-readable in a transaction-history list without
    the caller needing a second API call to GET /providers/{id}/ per row.
    """

    class Meta:
        model = PaymentProvider
        fields = ["id", "code", "name"]
        read_only_fields = fields


class PaymentTransactionListSerializer(serializers.ModelSerializer):
    """
    Handles the OUTPUT side of GET /payments/ (list) - see
    PaymentListCreateView.get() in views.py. Deliberately a richer field
    set than PaymentTransactionReadSerializer above (GET /payments/{id}/,
    the polling endpoint): this one is meant to back a transaction-history
    view/table, where seeing WHICH tenant/customer/provider each row
    belongs to (without a separate lookup per row) is the whole point,
    whereas the polling endpoint deliberately keeps that internal detail
    out of the response.

    tenant_id/customer_id/provider_id: same DRF trick as
    PaymentProviderReadSerializer.tenant_id above - Django gives every
    ForeignKey field an automatic "<field>_id" attribute alongside the
    "<field>" attribute itself (e.g. `transaction.provider`, a full
    PaymentProvider row that costs a query to load, vs
    `transaction.provider_id`, just the raw id already on this row).
    Naming these serializer fields "..._id" makes DRF read straight from
    those cheap attributes instead.

    `provider` (below) is different from those three: it's a NESTED
    serializer, not a plain id field, so it DOES need the full related
    PaymentProvider row to render code/name - see
    PaymentListCreateView.get() for how we avoid one extra database query
    PER transaction for this via `select_related("provider")`.
    """

    tenant_id = serializers.UUIDField(read_only=True)
    customer_id = serializers.UUIDField(read_only=True)
    provider_id = serializers.UUIDField(read_only=True)
    provider = PaymentTransactionProviderSerializer(read_only=True)

    class Meta:
        model = PaymentTransaction
        fields = [
            "id",
            "tenant_id",
            "customer_id",
            "provider_id",
            "provider",
            "reference",
            "amount",
            "currency",
            "state",
            "state_message",
            "provider_reference",
            "created_at",
            "updated_at",
        ]
        # Every field here is explicitly declared as read-only above,
        # either directly (the four "_id"/nested fields) or because this
        # serializer is only ever used to RENDER a list, never to validate
        # input - "tenant_id"/"customer_id"/"provider_id"/"provider" are
        # excluded from this list since DRF raises an error if a field is
        # both explicitly declared AND listed in read_only_fields (same
        # reasoning as PaymentProviderReadSerializer.read_only_fields
        # above).
        read_only_fields = [
            f for f in fields if f not in ("tenant_id", "customer_id", "provider_id", "provider")
        ]
