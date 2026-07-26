from rest_framework.response import Response
from rest_framework.views import APIView


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
