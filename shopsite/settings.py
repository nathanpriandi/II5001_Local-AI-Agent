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

    # Our components: agentic core, staff dashboard pages, buyer storefront pages
    "agentic.apps.AgenticConfig",
    "assistant.apps.AssistantDashboardConfig",
    "shopper.apps.ShopperConfig",
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
        "DIRS": [BASE_DIR / "templates"],  # project overrides (storefront navigation)
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
        # WAL lets the web server read while the agent worker writes; wait up to 30 s for locks
        "OPTIONS": {"timeout": 30, "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"},
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
OSCAR_SHOP_TAGLINE = "with local AI agents"
OSCAR_DEFAULT_CURRENCY = "USD"

# "AI Agents" menu in the dashboard's top navigation bar
OSCAR_DASHBOARD_NAVIGATION = OSCAR_DASHBOARD_NAVIGATION + [  # noqa: F405
    {
        "label": _("AI Agents"),
        "icon": "fas fa-robot",
        "children": [
            {"label": _("Agent Control Center"), "url_name": "assistant-control"},
            {"label": _("Store Agent chat"), "url_name": "assistant-index"},
            {"label": _("Policy and autonomy"), "url_name": "assistant-policy"},
        ],
    },
]

LOGIN_URL = "customer:login"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"  # no mail server needed

# ---------------------------------------------------------------------------
# Local AI model settings (Ollama)
# ---------------------------------------------------------------------------
OLLAMA_MODEL = os.environ.get("MODEL", "qwen3:4b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
ASSISTANT_LOW_STOCK_THRESHOLD = 5
AGENT_NUM_CTX = int(os.environ.get("AGENT_NUM_CTX", "8192"))
AGENT_SCAN_MINUTES = int(os.environ.get("AGENT_SCAN_MINUTES", "30"))  # 0 = no periodic stock scan
