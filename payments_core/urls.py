from django.urls import path

from .views import (
    AdyenPaymentsDetailsView,
    AdyenReturnView,
    AdyenWebhookView,
    PaymentDetailView,
    PaymentListCreateView,
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
    path("payments/", PaymentListCreateView.as_view(), name="payment-list-create"),
    path("payments/<uuid:pk>/", PaymentDetailView.as_view(), name="payment-detail"),
    # Webhook + redirect-return endpoints.
    #
    # webhooks/stripe/ and webhooks/adyen/ below are FIXED, single URLs -
    # no provider_id (or any other identifier) in the path, matching Odoo's
    # own webhook URLs exactly (/payment/stripe/webhook,
    # /payment/adyen/notification). An earlier version of this project put
    # provider_id in the path here (e.g. webhooks/stripe/<uuid:provider_id>/)
    # as a workaround for having no auth - that turned out to be
    # unnecessary and has been removed: see StripeWebhookView/
    # AdyenWebhookView in views.py for how we now find the right
    # tenant/provider from the webhook's OWN payload instead (via
    # PaymentTransaction._search_by_reference), the same way Odoo does.
    path("webhooks/stripe/return/", StripeReturnView.as_view(), name="stripe-return"),
    path("webhooks/stripe/", StripeWebhookView.as_view(), name="stripe-webhook"),
    path("webhooks/adyen/return/", AdyenReturnView.as_view(), name="adyen-return"),
    path(
        "webhooks/adyen/payments-details/",
        AdyenPaymentsDetailsView.as_view(),
        name="adyen-payments-details",
    ),
    path("webhooks/adyen/", AdyenWebhookView.as_view(), name="adyen-webhook"),
]
