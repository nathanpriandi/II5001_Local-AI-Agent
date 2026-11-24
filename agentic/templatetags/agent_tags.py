"""Small display helpers shared by the staff and buyer agent pages."""

from django import template

register = template.Library()

BADGES = {
    "active": "badge-primary", "fulfilled": "badge-success", "expired": "badge-secondary",
    "cancelled": "badge-danger", "superseded": "badge-secondary",
    "queued": "badge-secondary", "running": "badge-warning", "done": "badge-success", "failed": "badge-danger",
    "deal": "badge-success", "paid": "badge-success", "no_deal": "badge-secondary",
    "awaiting_seller": "badge-warning", "rejected_by_seller": "badge-danger",
    "awaiting_approval": "badge-warning", "approved": "badge-info", "received": "badge-success", "rejected": "badge-danger",
}

ACTORS = {"Store Agent": "actor-store", "Shopping Agent": "actor-shop", "Guard": "actor-guard",
          "Staff": "actor-staff", "Buyer": "actor-buyer", "Demo": "actor-demo"}


@register.filter
def status_badge(status):
    return BADGES.get(status, "badge-light")


@register.filter
def actor_class(actor):
    return ACTORS.get(actor, "actor-staff")


@register.filter
def nice(value):
    """awaiting_seller -> awaiting seller"""
    return str(value).replace("_", " ")
