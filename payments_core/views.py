from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PaymentProvider
from .serializers import (
    PaymentProviderCreateSerializer,
    PaymentProviderReadSerializer,
    PaymentProviderUpdateSerializer,
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
