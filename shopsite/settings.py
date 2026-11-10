"""
Django settings for the Oscar demo shop with a local AI "Store Ops Assistant".

Based on the Django Oscar getting-started guide:
https://django-oscar.readthedocs.io/en/latest/internals/getting_started.html
"""

import os
from pathlib import Path

from django.utils.translation import gettext_lazy as _
from oscar.defaults import *  # noqa: F401,F403  (Oscar's default settings)

BASE_DIR = Path(__file__).resolve().parent.parent

# Local development only; never reuse this key or DEBUG=True in production
SECRET_KEY = "django-insecure-local-demo-key-change-me"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.flatpages",

    # Django Oscar core apps
    "oscar.config.Shop",
    "oscar.apps.analytics.apps.AnalyticsConfig",
    "oscar.apps.checkout.apps.CheckoutConfig",
    "oscar.apps.address.apps.AddressConfig",
    "oscar.apps.shipping.apps.ShippingConfig",
    "oscar.apps.catalogue.apps.CatalogueConfig",
    "oscar.apps.catalogue.reviews.apps.CatalogueReviewsConfig",
    "oscar.apps.communication.apps.CommunicationConfig",
    "oscar.apps.partner.apps.PartnerConfig",
    "oscar.apps.basket.apps.BasketConfig",
    "oscar.apps.payment.apps.PaymentConfig",
    "oscar.apps.offer.apps.OfferConfig",
    "oscar.apps.order.apps.OrderConfig",
    "oscar.apps.customer.apps.CustomerConfig",
    "oscar.apps.search.apps.SearchConfig",
    "oscar.apps.voucher.apps.VoucherConfig",
    "oscar.apps.wishlists.apps.WishlistsConfig",
    "oscar.apps.dashboard.apps.DashboardConfig",
    "oscar.apps.dashboard.reports.apps.ReportsDashboardConfig",
    "oscar.apps.dashboard.users.apps.UsersDashboardConfig",
    "oscar.apps.dashboard.orders.apps.OrdersDashboardConfig",
    "oscar.apps.dashboard.catalogue.apps.CatalogueDashboardConfig",
    "oscar.apps.dashboard.offers.apps.OffersDashboardConfig",
    "oscar.apps.dashboard.partners.apps.PartnersDashboardConfig",
    "oscar.apps.dashboard.pages.apps.PagesDashboardConfig",
    "oscar.apps.dashboard.ranges.apps.RangesDashboardConfig",
    "oscar.apps.dashboard.reviews.apps.ReviewsDashboardConfig",
    "oscar.apps.dashboard.vouchers.apps.VouchersDashboardConfig",
    "oscar.apps.dashboard.communications.apps.CommunicationsDashboardConfig",
    "oscar.apps.dashboard.shipping.apps.ShippingDashboardConfig",

    # Third-party apps Oscar depends on
    "widget_tweaks",
    "haystack",
    "treebeard",
    "sorl.thumbnail",
    "django_tables2",

    # Our new component: the local AI agent inside the Oscar dashboard
    "assistant.apps.AssistantDashboardConfig",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "oscar.apps.basket.middleware.BasketMiddleware",
    "django.contrib.flatpages.middleware.FlatpageFallbackMiddleware",
]

ROOT_URLCONF = "shopsite.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Oscar context processors
                "oscar.apps.search.context_processors.search_form",
                "oscar.apps.checkout.context_processors.checkout",
                "oscar.apps.communication.notifications.context_processors.notifications",
                "oscar.core.context_processors.metadata",
            ],
        },
    },
]

WSGI_APPLICATION = "shopsite.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "ATOMIC_REQUESTS": True,
    }
}

AUTHENTICATION_BACKENDS = (
    "oscar.apps.customer.auth_backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
)

# Search index used by Oscar's catalogue pages. Whoosh is pure Python and stores
# its index in a local folder, so no Solr/Elasticsearch server is needed.
# (The "simple" backend cannot list "All products", which leaves the shop empty.)
HAYSTACK_CONNECTIONS = {
    "default": {
        "ENGINE": "haystack.backends.whoosh_backend.WhooshEngine",
        "PATH": BASE_DIR / "whoosh_index",
    },
}
# Keep the search index in sync when products are added or edited in the dashboard
HAYSTACK_SIGNAL_PROCESSOR = "haystack.signals.RealtimeSignalProcessor"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Taipei"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# ---------------------------------------------------------------------------
# Oscar shop settings
# ---------------------------------------------------------------------------
OSCAR_SHOP_NAME = "Campus Tech Store"
OSCAR_SHOP_TAGLINE = "Demo shop with a local AI assistant"
OSCAR_DEFAULT_CURRENCY = "USD"

# Add the AI assistant to the dashboard's top navigation bar
OSCAR_DASHBOARD_NAVIGATION = OSCAR_DASHBOARD_NAVIGATION + [  # noqa: F405
    {
        "label": _("AI Assistant"),
        "icon": "fas fa-robot",
        "url_name": "assistant-index",
    },
]

# ---------------------------------------------------------------------------
# Local AI model settings (Ollama)
# ---------------------------------------------------------------------------
OLLAMA_MODEL = os.environ.get("MODEL", "qwen3:4b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
ASSISTANT_LOW_STOCK_THRESHOLD = 5
