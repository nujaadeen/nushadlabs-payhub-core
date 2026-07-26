"""
URL configuration for payhub project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from payments_core.views import HealthCheckView

urlpatterns = [
    path('admin/', admin.site.urls),
    # Phase 0: confirms the project is up and wired together correctly.
    path('health/', HealthCheckView.as_view(), name='health-check'),
    # Phase 1: onboarding endpoints (POST/GET /providers/, GET/PATCH/DELETE
    # /providers/{id}/) - include() hands URL resolution for anything under
    # this path off to payments_core/urls.py, so this file doesn't have to
    # list every app's individual routes itself.
    path('', include('payments_core.urls')),
]
