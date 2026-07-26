from django.db import IntegrityError
from rest_framework import serializers

from .models import Customer, Tenant


class TenantSerializer(serializers.ModelSerializer):
    """
    Handles POST /tenants/ - both validating the input AND rendering the
    response. Unlike payments_core's provider endpoints (which split
    creation and reading into two separate serializer classes, because
    creating a provider also involves nested credential data), a Tenant is
    simple enough that one plain ModelSerializer can handle both
    directions here.
    """

    class Meta:
        model = Tenant
        fields = ["id", "name", "created_at"]
        # "id" is a UUIDField the model itself generates (see
        # Tenant.id in tenants/models.py - default=uuid.uuid4), and
        # "created_at" is auto_now_add - neither is something an API
        # caller should be allowed to set themselves, so both are
        # read-only: they show up in responses, but sending them in a
        # request body is silently ignored rather than erroring.
        read_only_fields = ["id", "created_at"]


class CustomerSerializer(serializers.ModelSerializer):
    """
    Handles POST /customers/.

    Django/DRF magic worth calling out: `tenant` is a ForeignKey on the
    Customer model (see tenants/models.py). Just by listing "tenant" in
    Meta.fields below, DRF's ModelSerializer automatically inspects the
    model and turns that ForeignKey into a PrimaryKeyRelatedField - which
    means the API expects (and validates) a plain tenant UUID string in
    the request body. DRF checks both that it's a well-formed UUID AND
    that a Tenant with that id actually exists, returning a clean 400 if
    either check fails - we get all of that for free, without writing any
    validation code for it ourselves.

    A second piece of free validation comes from the SAME field-name
    match: DRF also auto-attaches a UniqueTogetherValidator for
    Customer.Meta.unique_together = ("tenant", "reference") - see the
    create() override below for the full explanation, including why this
    DOESN'T happen for payments_core's provider serializer.
    """

    class Meta:
        model = Customer
        fields = ["id", "tenant", "reference", "created_at"]
        read_only_fields = ["id", "created_at"]

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except IntegrityError:
            # DRF magic worth knowing about, and why this except block is
            # mostly a BACKSTOP rather than the primary defense: because
            # this serializer's `tenant` field is a real, auto-generated
            # PrimaryKeyRelatedField with the SAME name as the model's
            # actual ForeignKey (see the class docstring above) - unlike
            # payments_core/serializers.py's PaymentProviderCreateSerializer,
            # which manually declares a differently-named `tenant_id` field
            # instead - DRF's ModelSerializer was able to match "tenant"
            # against Customer.Meta.unique_together = ("tenant",
            # "reference") and automatically attach a UniqueTogetherValidator
            # to this serializer. That validator runs during is_valid()
            # (an .exists() pre-check) and already catches the common case
            # - one request, a duplicate that already exists - with a
            # clean 400, before create() is ever even called. Try this
            # yourself: POST the same (tenant, reference) twice and the
            # second one fails at is_valid(), never reaching this method.
            #
            # This except block is still worth keeping, though, as a
            # backstop for the genuine race condition DRF's validator
            # CAN'T catch: two requests for the same (tenant, reference)
            # both passing that validator's pre-check at the exact same
            # instant, then both trying to insert. Only the DATABASE's own
            # unique_together constraint (enforced at insert time, not
            # ahead of time) can catch THAT reliably - the same reasoning
            # behind ProviderListCreateView.post() catching IntegrityError
            # for PaymentProvider's (tenant, code) uniqueness in
            # payments_core/views.py; that serializer has to do it in the
            # view (for EVERY case, not just the race condition) precisely
            # because its "tenant_id" field name mismatch means it doesn't
            # get DRF's automatic validator at all.
            raise serializers.ValidationError(
                {"reference": "A customer with this reference already exists for this tenant."}
            )
