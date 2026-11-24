"""
Run queue: every piece of agent work is an AgentRun row processed by the agent
worker (python manage.py agent_worker). The UI polls the row to show live steps.
"""

from django.db import transaction
from django.utils import timezone

from .guard import sign
from .models import AgentRun, Mandate

# Interactive runs (a buyer or staff member is waiting) go before background runs.
PRIORITY = {"plan": 1, "parse_delegation": 1, "negotiate": 2, "fulfil_delegations": 3,
            "stock_check": 4, "briefing": 5, "scan": 6}

DEFAULT_POLICY = {
    "reorder_mode": "auto",           # suggest | ask | auto
    "reorder_auto_limit": 150,        # USD per purchase order
    "negotiation_mode": "auto",       # off | ask | auto
    "max_discount_pct": 10,
    "min_qty_for_discount": 5,
    "min_days_cover_after_sale": 14,
    "max_rounds": 3,
}


def enqueue(kind, user=None, product=None, payload=None, dedupe=False):
    """Create a run after the current transaction commits (so the worker sees committed data)."""
    def create():
        if dedupe and AgentRun.objects.filter(kind=kind, product=product,
                                              status__in=[AgentRun.QUEUED, AgentRun.RUNNING]).exists():
            return
        AgentRun.objects.create(kind=kind, user=user, product=product, payload=payload or {},
                                priority=PRIORITY.get(kind, 5))
    transaction.on_commit(create)


def enqueue_now(kind, user=None, product=None, payload=None):
    """Create a run immediately and return it (for UI requests that poll it)."""
    return AgentRun.objects.create(kind=kind, user=user, product=product, payload=payload or {},
                                   priority=PRIORITY.get(kind, 5))


def current_policy():
    """The active seller policy mandate. If none was ever signed, the first superuser signs the default."""
    policy = Mandate.objects.filter(kind=Mandate.POLICY, status=Mandate.ACTIVE).order_by("-created").first()
    if policy is None:
        from django.contrib.auth import get_user_model
        admin = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        if admin is not None:
            policy = sign_policy(admin, dict(DEFAULT_POLICY))
    return policy


@transaction.atomic
def sign_policy(user, terms):
    """Staff signs a new seller policy; the previous one is superseded."""
    Mandate.objects.filter(kind=Mandate.POLICY, status=Mandate.ACTIVE).update(status=Mandate.SUPERSEDED)
    mandate = Mandate(kind=Mandate.POLICY, side="seller", owner=user, terms=terms)
    mandate.signature = sign(mandate)
    mandate.save()
    return mandate


def finish(run, result=None, error=""):
    run.result = result if result is not None else run.result
    run.error = error
    run.status = AgentRun.FAILED if error else AgentRun.DONE
    run.finished = timezone.now()
    run.save()
