from django.urls import path

from .views import (
    PaymentCreateView,
    PaymentDetailView,
    ProviderDetailView,
    ProviderListCreateView,
)

urlpatterns = [
    path("providers/", ProviderListCreateView.as_view(), name="provider-list-create"),
    # <uuid:pk> - Django's UUID path converter: it only matches a
    # properly-formatted UUID in the URL and automatically converts it to a
    # Python uuid.UUID object before passing it into the view as `pk`. If
    # the URL segment isn't a valid UUID, this pattern simply doesn't match
    # (Django returns a 404 rather than passing a bad value into the view).
    path("providers/<uuid:pk>/", ProviderDetailView.as_view(), name="provider-detail"),
    path("payments/", PaymentCreateView.as_view(), name="payment-create"),
    path("payments/<uuid:pk>/", PaymentDetailView.as_view(), name="payment-detail"),
]
