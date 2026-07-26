from django.urls import path

from .views import CustomerListCreateView, TenantListCreateView

urlpatterns = [
    path("tenants/", TenantListCreateView.as_view(), name="tenant-list-create"),
    path("customers/", CustomerListCreateView.as_view(), name="customer-list-create"),
]
