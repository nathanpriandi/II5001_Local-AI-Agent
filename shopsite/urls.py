"""URL routes: Oscar's shop and dashboard, plus the AI assistant pages."""

from django.apps import apps
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # AI assistant, mounted inside the dashboard area (staff only)
    path("dashboard/assistant/", include(apps.get_app_config("assistant_dashboard").urls[0])),
    # Everything else: Oscar storefront + dashboard
    path("", include(apps.get_app_config("oscar").urls[0])),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
