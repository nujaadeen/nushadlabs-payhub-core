from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from tenants.models import Customer

from .exceptions import PaymentProviderRequestError
from .models import PaymentProvider, PaymentTransaction
from .serializers import (
    PaymentProviderCreateSerializer,
    PaymentProviderReadSerializer,
    PaymentProviderUpdateSerializer,
    PaymentTransactionCreateSerializer,
    PaymentTransactionReadSerializer,
)


class HealthCheckView(APIView):
    """
    A DRF APIView (as opposed to a plain Django view) gets us DRF's request
    parsing, content negotiation, and browsable-API rendering "for free" -
    even though this view is trivial, using APIView here matches how every
    real endpoint we add in later phases will be built.

    Because REST_FRAMEWORK's DEFAULT_PERMISSION_CLASSES is AllowAny (see
    settings.py), no auth/permission check runs before `get()` below - any
    caller, authenticated or not, can hit this endpoint.
    """

    def get(self, request):
        return Response({"status": "ok"})


class ProviderListCreateView(APIView):
    """Handles GET /providers/ and POST /providers/."""

    def get(self, request):
        # We have no auth, so there's no logged-in tenant to scope this
        # query to automatically (Odoo would infer this from the current
        # user's company). Instead we require the caller to tell us
        # explicitly which tenant's providers they want, via ?tenant_id=.
        tenant_id = request.query_params.get("tenant_id")
        if not tenant_id:
            return Response(
                {"tenant_id": "This query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # QuerySets are lazy - .filter() below doesn't hit the database yet,
        # it just builds up a SQL query description. The database is only
        # actually queried once we do something that needs the results,
        # which happens inside the serializer when it iterates over
        # `providers` to build the response list.
        providers = PaymentProvider.objects.filter(tenant_id=tenant_id)
        serializer = PaymentProviderReadSerializer(providers, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = PaymentProviderCreateSerializer(data=request.data)
        # raise_exception=True: if validation fails, DRF raises a
        # ValidationError internally, which DRF's exception handler catches
        # and turns into a 400 response with the validation errors as the
        # body - we never see that response construction here, it happens
        # for us.
        serializer.is_valid(raise_exception=True)
        try:
            provider = serializer.save()
        except IntegrityError:
            # This catches the database-level (tenant, code) uniqueness
            # constraint (see PaymentProvider.Meta.unique_together). We
            # don't pre-check this in the serializer because that check
            # would have a race condition: two requests for the same
            # tenant+code could both pass a "does this already exist?" check
            # at the same instant, then both try to insert. The database
            # constraint is the only thing that can catch that reliably.
            # Without this except block, that failure would surface to the
            # API caller as a raw 500 Internal Server Error with a Postgres
            # error message, instead of a clean, understandable 400.
            return Response(
                {"code": "A provider with this code already exists for this tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            PaymentProviderReadSerializer(provider).data, status=status.HTTP_201_CREATED
        )


class ProviderDetailView(APIView):
    """Handles GET/PATCH/DELETE on /providers/{id}/."""

    def get_object(self, pk):
        # get_object_or_404 is a Django shortcut: it runs
        # PaymentProvider.objects.get(pk=pk) and, if that raises
        # PaymentProvider.DoesNotExist, converts it into an Http404
        # exception instead. DRF's exception handler catches Http404 the
        # same way it catches ValidationError, and turns it into a clean
        # 404 response - so we never have to write that error-handling
        # ourselves.
        return get_object_or_404(PaymentProvider, pk=pk)

    def get(self, request, pk):
        provider = self.get_object(pk)
        return Response(PaymentProviderReadSerializer(provider).data)

    def patch(self, request, pk):
        provider = self.get_object(pk)
        # partial=True tells DRF "treat every field on this serializer as
        # optional for this call" - without it, a ModelSerializer normally
        # requires every non-nullable field to be present, which would make
        # PATCH behave like a full PUT replace instead of a true partial
        # update.
        serializer = PaymentProviderUpdateSerializer(provider, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        provider = serializer.save()
        return Response(PaymentProviderReadSerializer(provider).data)

    def delete(self, request, pk):
        provider = self.get_object(pk)
        # Soft delete, not a real DELETE: mirrors Odoo's
        # _unlink_except_master_data, which blocks hard-deleting a provider
        # outright. We flip it to disabled/unpublished instead. This also
        # matches the on_delete=PROTECT we set on PaymentToken.provider and
        # PaymentTransaction.provider (payments_core/models.py) - the
        # database itself would refuse a hard delete of a provider that
        # still has tokens or transactions pointing at it, so a real DELETE
        # here would fail for any provider that's actually been used.
        provider.state = "disabled"
        provider.is_published = False
        provider.save(update_fields=["state", "is_published", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PaymentCreateView(APIView):
    """
    Handles POST /payments/ - mirrors Odoo's
    PaymentPortal._create_transaction() -> payment.transaction.create() ->
    tx._get_processing_values() -> adapter._send_payment_request() flow,
    simplified to the "online_redirect" operation only.
    """

    def post(self, request):
        serializer = PaymentTransactionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        provider = get_object_or_404(PaymentProvider, pk=data["provider_id"])

        if provider.tenant_id != data["tenant_id"]:
            # We have no auth, so nothing stops a caller from passing a
            # provider_id that belongs to a DIFFERENT tenant than the
            # tenant_id they also passed - this check is our only guard
            # against that mix-up (e.g. tenant A accidentally, or
            # maliciously, creating a payment against tenant B's Stripe
            # account).
            return Response(
                {"provider_id": "This provider does not belong to the given tenant."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Named _ensure_provider_is_not_disabled to match Odoo's method
            # of the same name exactly (see payments_core/models.py).
            provider._ensure_provider_is_not_disabled()
        except ValueError as exc:
            return Response({"provider_id": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Mirrors the min/max amount filtering Odoo's _get_compatible_providers
        # does when picking which providers a customer COULD use for a given
        # amount - simplified here to a straight comparison, since we're
        # validating the ONE provider the caller already chose, not
        # auto-selecting from several candidates.
        if provider.minimum_amount is not None and data["amount"] < provider.minimum_amount:
            return Response(
                {"amount": f"Amount must be at least {provider.minimum_amount}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provider.maximum_amount is not None and data["amount"] > provider.maximum_amount:
            return Response(
                {"amount": f"Amount must be at most {provider.maximum_amount}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer = get_object_or_404(
            Customer, pk=data["customer_id"], tenant_id=data["tenant_id"]
        )

        reference = PaymentTransaction._compute_reference(
            provider_code=provider.code, tenant_id=data["tenant_id"]
        )

        # Step 1: create the draft row and let it COMMIT before we make any
        # external call. We deliberately do NOT wrap this create() and the
        # adapter call below in a single transaction.atomic() block - that
        # would be the natural beginner instinct ("wrap the whole thing so
        # it's all-or-nothing!"), but atomic() is for grouping DATABASE
        # writes together. The adapter call a few lines down is a NETWORK
        # REQUEST to Stripe/Adyen's servers, which can take seconds, or
        # time out, or hang. Holding a database transaction (and its locks,
        # and its connection) open for however long that network call takes
        # would be pure downside with no upside - so instead we commit the
        # draft row first, make the external call as a separate step, then
        # do a second, separate save with whatever the call returned.
        txn = PaymentTransaction.objects.create(
            tenant_id=data["tenant_id"],
            provider=provider,
            customer=customer,
            reference=reference,
            currency=data["currency"].upper(),
            amount=data["amount"],
            operation="online_redirect",
            return_url=data["return_url"],
        )

        # Step 2: the external call - NOT inside a database transaction.
        try:
            processing_values = txn._get_processing_values()
        except PaymentProviderRequestError as exc:
            txn._set_error(state_message=str(exc))
            return Response(
                {
                    "id": str(txn.id),
                    "reference": txn.reference,
                    "state": txn.state,
                    "state_message": txn.state_message,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Step 3: a second, separate save recording what the adapter call
        # returned - this only runs once we know the external call actually
        # succeeded.
        txn.provider_reference = processing_values.get("provider_reference")
        txn.provider_data = processing_values.get("raw_response")
        txn.save(update_fields=["provider_reference", "provider_data", "updated_at"])
        txn._set_pending()

        return Response(
            {
                "id": str(txn.id),
                "reference": txn.reference,
                "state": txn.state,
                "redirect_url": processing_values.get("redirect_url"),
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentDetailView(APIView):
    """
    Handles GET /payments/{id}/ - the polling endpoint an API caller uses to
    check a payment's current status (mirrors Odoo's `/payment/status` page
    polling pattern, but as a plain JSON endpoint since we have no UI).

    No webhook handling exists yet (that's Phase 3), so right now a
    transaction created via POST /payments/ will sit in `state="pending"`
    forever once the customer reaches the provider's page - there's nothing
    yet that moves it on to "done"/"authorized"/"error" after that point.
    That's expected for this phase.
    """

    def get(self, request, pk):
        txn = get_object_or_404(PaymentTransaction, pk=pk)
        return Response(PaymentTransactionReadSerializer(txn).data)
