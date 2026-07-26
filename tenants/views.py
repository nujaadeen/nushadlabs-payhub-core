from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import CustomerSerializer, TenantSerializer


class TenantCreateView(APIView):
    """
    Handles POST /tenants/ - creates a Tenant.

    A plain DRF APIView with an explicit post() method, matching the style
    used throughout payments_core (see e.g. ProviderListCreateView in
    payments_core/views.py) rather than DRF's generics.CreateAPIView
    shortcut - both would work here, but this project has consistently
    picked the more explicit, spelled-out style everywhere else, so we
    match that for consistency.

    No authentication/permission checks run before post() below - see
    REST_FRAMEWORK's DEFAULT_AUTHENTICATION_CLASSES/DEFAULT_PERMISSION_CLASSES
    in settings.py, which apply project-wide and make every endpoint
    AllowAny by default, this one included.
    """

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


class CustomerCreateView(APIView):
    """
    Handles POST /customers/ - creates a Customer under a given tenant.

    Unlike ProviderListCreateView.post() in payments_core/views.py (which
    explicitly catches IntegrityError to handle the (tenant, code)
    uniqueness constraint), there's no try/except here for the equivalent
    (tenant, reference) constraint - CustomerSerializer.create() already
    catches it and re-raises a clean serializers.ValidationError itself
    (see tenants/serializers.py), which DRF's exception handling already
    knows how to turn into a 400 - so there's nothing extra for this view
    to do.
    """

    def post(self, request):
        serializer = CustomerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
        return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)
