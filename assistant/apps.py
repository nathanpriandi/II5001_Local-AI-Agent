"""
App config for the AI Store Ops Assistant.

It subclasses Oscar's OscarDashboardConfig, so it plugs into Oscar the same way
Oscar's own dashboard apps do: every URL is protected by Oscar's permission
decorator (staff only) and the menu entry in OSCAR_DASHBOARD_NAVIGATION is
shown or hidden by Oscar's navigation code.
"""

from django.urls import path
from django.utils.translation import gettext_lazy as _
from oscar.core.application import OscarDashboardConfig


class AssistantDashboardConfig(OscarDashboardConfig):
    name = "assistant"
    label = "assistant_dashboard"
    verbose_name = _("AI Store Ops Assistant")

    # Only staff accounts can open any assistant page
    default_permissions = ["is_staff"]

    def get_urls(self):
        # Imported here because views use models, which are not ready at import time
        from . import control, views

        urls = [
            path("", views.index, name="assistant-index"),
            path("chat/", views.chat, name="assistant-chat"),
            path("confirm/<str:action_id>/", views.confirm_action, name="assistant-confirm"),
            path("cancel/<str:action_id>/", views.cancel_action, name="assistant-cancel"),
            path("reset/", views.reset, name="assistant-reset"),
            path("snapshot/", views.snapshot, name="assistant-snapshot"),
            # Agent Control Center
            path("control/", control.control, name="assistant-control"),
            path("control/panel/", control.control_panel, name="assistant-control-panel"),
            path("control/po/<int:pk>/<str:action>/", control.po_action, name="assistant-po"),
            path("control/deal/<int:pk>/<str:action>/", control.deal_action, name="assistant-deal"),
            path("control/run/<str:kind>/", control.start_run, name="assistant-run"),
            path("control/tamper/<int:pk>/", control.tamper, name="assistant-tamper"),
            path("policy/", control.policy, name="assistant-policy"),
        ]
        # post_process_urls wraps each view with Oscar's permission decorator
        return self.post_process_urls(urls)
