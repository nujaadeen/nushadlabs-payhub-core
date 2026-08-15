from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Customer, Tenant
from .serializers import CustomerReadSerializer, CustomerSerializer, TenantSerializer


class TenantListCreateView(APIView):
    """
    Handles GET /tenants/ (list) and POST /tenants/ (create) - renamed from
    TenantCreateView now that this view handles both, matching the
    "ListCreateView does both GET and POST" naming/style already used by
    ProviderListCreateView in payments_core/views.py.

    A plain DRF APIView with explicit get()/post() methods, matching the
    style used throughout payments_core rather than DRF's
    generics.ListCreateAPIView shortcut - both would work here, but this
    project has consistently picked the more explicit, spelled-out style
    everywhere else, so we match that for consistency.

    No authentication/permission checks run before either method below -
    see REST_FRAMEWORK's DEFAULT_AUTHENTICATION_CLASSES/DEFAULT_PERMISSION_CLASSES
    in settings.py, which apply project-wide and make every endpoint
    AllowAny by default, this one included.
    """

    def get(self, request):
        # No filtering needed here, unlike GET /customers/ below: there's
        # no "tenant of a tenant" concept anywhere in this project, so
        # every Tenant row is always visible. No pagination either - this
        # project is a small demo/admin tool, not a public-facing API
        # expecting thousands of tenants (see README for the same
        # reasoning applied to GET /payments/).
        tenants = Tenant.objects.all()
        # many=True tells DRF's ModelSerializer "serialize a whole
        # iterable of these, not just one instance" - it then returns a
        # plain JSON array of objects, each shaped exactly like a single
        # POST /tenants/ response already looks (same TenantSerializer,
        # reused as-is - no new serializer needed since its fields already
        # match what a list view needs here).
        serializer = TenantSerializer(tenants, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = TenantSerializer(data=request.data)
        # raise_exception=True: if validation fails (e.g. "name" missing),
        # DRF raises a ValidationError internally, which DRF's own
        # exception handling catches and turns into a 400 response with
        # the validation errors as the body - we never see that response
        # construction here, it happens for us.
        serializer.is_valid(raise_exception=True)
        tenant = serializer.save()
        return Response(TenantSerializer(tenant).data, status=status.HTTP_201_CREATED)


class CustomerListCreateView(APIView):
    """
    Handles GET /customers/ (list) and POST /customers/ (create) - renamed
    from CustomerCreateView now that this view handles both, same
    rationale as TenantListCreateView above.

    Unlike ProviderListCreateView.post() in payments_core/views.py (which
    explicitly catches IntegrityError to handle the (tenant, code)
    uniqueness constraint), there's no try/except here for the equivalent
    (tenant, reference) constraint - CustomerSerializer.create() already
    catches it and re-raises a clean serializers.ValidationError itself
    (see tenants/serializers.py), which DRF's exception handling already
    knows how to turn into a 400 - so there's nothing extra for this view
    to do.
    """

    def get(self, request):
        # Required, not optional: mirrors the same tenant-scoping
        # discipline as ProviderListCreateView.get() in
        # payments_core/views.py (GET /providers/) - we have no auth, so
        # there's no logged-in tenant to scope this to automatically, and
        # silently returning every customer across every tenant would be
        # an easy way to accidentally leak one tenant's customer list to
        # another. Requiring the caller to say which tenant they mean
        # keeps that scoping explicit instead of accidental.
        tenant_id = request.query_params.get("tenant_id")
        if not tenant_id:
            return Response(
                {"tenant_id": "This query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # QuerySets are lazy - .filter() below doesn't hit the database
        # yet, it just builds up a SQL query description. It's only
        # actually run once CustomerReadSerializer iterates over it to
        # build the response list.
        customers = Customer.objects.filter(tenant_id=tenant_id)
        serializer = CustomerReadSerializer(customers, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CustomerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
        return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)
