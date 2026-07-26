from django.urls import path

from .views import (
    AdyenPaymentsDetailsView,
    AdyenReturnView,
    AdyenWebhookView,
    PaymentCreateView,
    PaymentDetailView,
    ProviderDetailView,
    ProviderListCreateView,
    StripeReturnView,
    StripeWebhookView,
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
    # Phase 3: webhook + redirect-return endpoints. See StripeWebhookView /
    # AdyenWebhookView in views.py for why provider_id is embedded in the
    # webhook URLs specifically (we have no auth, so this is how we know
    # which tenant's webhook secret to verify against).
    path("webhooks/stripe/return/", StripeReturnView.as_view(), name="stripe-return"),
    path(
        "webhooks/stripe/<uuid:provider_id>/",
        StripeWebhookView.as_view(),
        name="stripe-webhook",
    ),
    path("webhooks/adyen/return/", AdyenReturnView.as_view(), name="adyen-return"),
    path(
        "webhooks/adyen/payments-details/",
        AdyenPaymentsDetailsView.as_view(),
        name="adyen-payments-details",
    ),
    path(
        "webhooks/adyen/<uuid:provider_id>/",
        AdyenWebhookView.as_view(),
        name="adyen-webhook",
    ),
]
