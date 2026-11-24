"""
Store events that wake the agents up (no one has to ask):
  - any stock change -> Shopping Agent checks delegations for that product
                     -> Store Agent checks whether to reorder
Runs are created after the database transaction commits.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from oscar.core.loading import get_model

from .models import Mandate, PurchaseOrder

StockRecord = get_model("partner", "StockRecord")

# Set to True while seeding demo data so bulk inserts do not queue agent work
SUPPRESS = {"value": False}


@receiver(post_save, sender=StockRecord)
def stock_changed(sender, instance, **kwargs):
    if SUPPRESS["value"]:
        return
    from .runs import enqueue
    product = instance.product
    avail = (instance.num_in_stock or 0) - (instance.num_allocated or 0)
    if avail > 0 and Mandate.objects.filter(kind=Mandate.INTENT, status=Mandate.ACTIVE, product=product).exists():
        enqueue("fulfil_delegations", product=product, dedupe=True)
    threshold = instance.low_stock_threshold if instance.low_stock_threshold is not None else 5
    open_po = PurchaseOrder.objects.filter(product=product, status__in=[PurchaseOrder.AWAITING, PurchaseOrder.APPROVED]).exists()
    if avail <= threshold and not open_po:
        enqueue("stock_check", product=product, dedupe=True)
