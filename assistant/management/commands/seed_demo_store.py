"""
Seed the Oscar shop with demo data:
  - a staff account for the dashboard
  - a "Campus Tech Store" catalogue (categories, 16 products, prices, stock)
  - about 40 orders spread over the last 14 days

Run:  python manage.py seed_demo_store
Re-running it wipes the demo catalogue and orders and recreates them.
"""

import random
import shutil
from pathlib import Path
from datetime import timedelta
from decimal import Decimal

import oscar
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from oscar.apps.catalogue.categories import create_from_breadcrumbs
from oscar.core.loading import get_model

Product = get_model("catalogue", "Product")
ProductClass = get_model("catalogue", "ProductClass")
ProductCategory = get_model("catalogue", "ProductCategory")
Category = get_model("catalogue", "Category")
Partner = get_model("partner", "Partner")
StockRecord = get_model("partner", "StockRecord")
Order = get_model("order", "Order")
Line = get_model("order", "Line")

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin12345"

# (title, upc, category breadcrumb, price USD, stock, low_stock_threshold, popularity weight, description)
PRODUCTS = [
    ("Raspberry Pi 5 Starter Kit (8GB)", "RPI5-KIT-8", "Electronics > Single-Board Computers", "119.00", 3, 5, 9,
     "Raspberry Pi 5 with 8GB RAM, case, 27W USB-C power supply and 64GB microSD card."),
    ("Arduino Uno R4 WiFi", "ARD-UNO-R4", "Electronics > Microcontrollers", "27.50", 22, 5, 7,
     "Arduino Uno R4 with ESP32-S3 WiFi/Bluetooth module and 12x8 LED matrix."),
    ("ESP32 DevKit V1 (3-pack)", "ESP32-3PK", "Electronics > Microcontrollers", "18.90", 2, 6, 8,
     "Three ESP32 development boards with WiFi and Bluetooth, ideal for IoT prototypes."),
    ("DHT22 Temperature & Humidity Sensor", "SNS-DHT22", "Electronics > Sensors", "6.50", 40, 10, 6,
     "Digital temperature and humidity sensor, -40 to 80 C range."),
    ("MQ-135 Air Quality Sensor", "SNS-MQ135", "Electronics > Sensors", "4.90", 0, 8, 4,
     "Gas sensor module for air quality monitoring (NH3, CO2, benzene)."),
    ("Mechanical Keyboard (Brown Switches)", "KB-MECH-BRN", "Computer Accessories > Keyboards", "79.00", 14, 4, 5,
     "Tenkeyless mechanical keyboard with tactile brown switches and USB-C cable."),
    ("Wireless Ergonomic Mouse", "MS-ERGO-WL", "Computer Accessories > Mice", "39.90", 4, 5, 6,
     "Vertical ergonomic mouse, 2.4 GHz and Bluetooth, rechargeable."),
    ("USB-C 7-in-1 Hub", "HUB-USBC-7", "Computer Accessories > Hubs & Adapters", "34.00", 25, 6, 8,
     "HDMI 4K, 3x USB-A, SD/microSD and 100W power delivery passthrough."),
    ("Aluminium Laptop Stand", "STD-LAPTOP-AL", "Computer Accessories > Stands", "29.00", 18, 4, 3,
     "Adjustable aluminium stand for 10 to 17 inch laptops."),
    ("27-inch QHD Monitor", "MON-27-QHD", "Displays > Monitors", "259.00", 6, 2, 2,
     "27-inch 2560x1440 IPS monitor, 75Hz, HDMI and DisplayPort."),
    ("1080p Webcam with Mic", "CAM-1080", "Computer Accessories > Webcams", "45.00", 1, 4, 5,
     "Full HD webcam with stereo microphone and privacy shutter."),
    ("Noise-Cancelling Headphones", "AUD-ANC-01", "Audio > Headphones", "149.00", 9, 3, 4,
     "Over-ear Bluetooth headphones with active noise cancelling, 30h battery."),
    ("1TB NVMe SSD", "SSD-NVME-1TB", "Storage > SSDs", "69.00", 12, 5, 6,
     "PCIe Gen4 NVMe M.2 SSD, up to 7000 MB/s read."),
    ("20,000 mAh Power Bank", "PWR-BANK-20K", "Power > Power Banks", "32.00", 30, 8, 7,
     "USB-C PD 65W power bank, can charge laptops."),
    ("Smart Plug with Energy Monitoring", "IOT-PLUG-EM", "Smart Home > Plugs", "15.90", 5, 6, 5,
     "WiFi smart plug that reports real-time power usage, works with Home Assistant."),
    ("Breadboard & Jumper Wire Kit", "PROTO-BB-KIT", "Electronics > Prototyping", "9.90", 55, 10, 6,
     "830-point breadboard with 120 jumper wires."),
]

ORDER_STATUSES = ["Pending", "Being processed", "Complete", "Complete", "Complete"]


class Command(BaseCommand):
    help = "Create a demo catalogue, stock and order history for the AI assistant demo."

    def handle(self, *args, **options):
        self._copy_missing_image()
        # Create all data in one transaction, then index it once it is committed
        with transaction.atomic():
            self._seed()
        # Rebuild the search index so the storefront catalogue lists every product
        call_command("rebuild_index", interactive=False, verbosity=0)
        self.stdout.write(self.style.SUCCESS(
            f"Demo store ready: {Product.objects.count()} products, {Order.objects.count()} orders.\n"
            f"Dashboard login: {ADMIN_EMAIL} / {ADMIN_PASSWORD}"
        ))

    def _copy_missing_image(self):
        """Copy Oscar's placeholder product image into MEDIA_ROOT.

        Oscar normally creates a symbolic link on first use, but Windows only allows
        symlinks for administrators, which makes the catalogue page crash.
        """
        src = Path(oscar.__file__).parent / "static" / "oscar" / "img" / "image_not_found.jpg"
        dst = Path(settings.MEDIA_ROOT) / settings.OSCAR_MISSING_IMAGE_URL
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

    def _seed(self):
        rng = random.Random(7)  # fixed seed = same demo data every time

        # 1. Staff account for the dashboard (Oscar logs in by email)
        User = get_user_model()
        admin, created = User.objects.get_or_create(
            username="admin", defaults={"email": ADMIN_EMAIL, "is_staff": True, "is_superuser": True}
        )
        if created:
            admin.set_password(ADMIN_PASSWORD)
            admin.save()

        # 2. Site name shown in the shop header
        Site.objects.filter(pk=1).update(domain="127.0.0.1:8000", name="Campus Tech Store")

        # 3. Countries (needed by Oscar's checkout), only on the first run.
        #    Oscar's --initial-only flag calls sys.exit(), so check the table ourselves.
        if not get_model("address", "Country").objects.exists():
            call_command("oscar_populate_countries", verbosity=0)

        # 4. Wipe previous demo data so the command can be re-run
        Line.objects.all().delete()
        Order.objects.all().delete()
        StockRecord.objects.all().delete()
        Product.objects.all().delete()
        Category.objects.all().delete()

        product_class, _ = ProductClass.objects.get_or_create(
            name="Tech Product", defaults={"requires_shipping": True, "track_stock": True}
        )
        partner, _ = Partner.objects.get_or_create(name="Campus Tech Warehouse")

        # 5. Products with categories and stock records
        stockrecords = []
        for title, upc, crumbs, price, stock, threshold, weight, desc in PRODUCTS:
            product = Product.objects.create(
                title=title, upc=upc, description=desc, product_class=product_class,
                structure=Product.STANDALONE, is_public=True,
            )
            category = create_from_breadcrumbs(crumbs)
            ProductCategory.objects.create(product=product, category=category)
            sr = StockRecord.objects.create(
                product=product, partner=partner, partner_sku=upc, price_currency="USD",
                price=Decimal(price), num_in_stock=stock, num_allocated=0,
                low_stock_threshold=threshold,
            )
            stockrecords.append((sr, weight))

        # 6. Order history over the last 14 days (popular items appear more often)
        site = Site.objects.get(pk=1)
        now = timezone.now()
        population = [sr for sr, _ in stockrecords]
        weights = [w for _, w in stockrecords]
        for n in range(40):
            placed = now - timedelta(days=rng.uniform(0, 14))
            items = {}
            for sr in rng.choices(population, weights=weights, k=rng.randint(1, 3)):
                items[sr] = items.get(sr, 0) + rng.randint(1, 2)
            total = sum(sr.price * qty for sr, qty in items.items())
            order = Order.objects.create(
                number=f"{100001 + n}", site=site, currency="USD",
                total_incl_tax=total, total_excl_tax=total,
                shipping_incl_tax=Decimal("0.00"), shipping_excl_tax=Decimal("0.00"),
                status=rng.choice(ORDER_STATUSES), guest_email=f"student{n + 1}@campus.edu",
                date_placed=placed,
            )
            for sr, qty in items.items():
                line_total = sr.price * qty
                Line.objects.create(
                    order=order, partner=partner, partner_name=partner.name,
                    partner_sku=sr.partner_sku, stockrecord=sr, product=sr.product,
                    title=sr.product.title, upc=sr.product.upc, quantity=qty,
                    line_price_incl_tax=line_total, line_price_excl_tax=line_total,
                    line_price_before_discounts_incl_tax=line_total,
                    line_price_before_discounts_excl_tax=line_total,
                    unit_price_incl_tax=sr.price, unit_price_excl_tax=sr.price,
                    status=order.status, num_allocated=0,
                )
