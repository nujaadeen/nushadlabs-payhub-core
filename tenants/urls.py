from django.urls import path

from .views import CustomerCreateView, TenantCreateView

urlpatterns = [
    path("tenants/", TenantCreateView.as_view(), name="tenant-create"),
    path("customers/", CustomerCreateView.as_view(), name="customer-create"),
]
