from django.apps import AppConfig


class AgenticConfig(AppConfig):
    name = "agentic"
    verbose_name = "Agentic layer (mandates, wallet, agents)"
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        from . import signals  # noqa: F401  connect store event triggers
