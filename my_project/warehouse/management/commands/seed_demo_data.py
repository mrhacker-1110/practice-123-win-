from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from warehouse.models import Category, Customer, Organization, Product, Supplier, UserProfile


class Command(BaseCommand):
    help = "Populate the warehouse with demo users, contacts, categories, and products for testing."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="demo-admin")
        parser.add_argument("--email", default="demo@warehouse.local")
        parser.add_argument("--password", default="Demo12345!")
        parser.add_argument("--company", default="Kuba Warehouse")
        parser.add_argument("--plan", default="pro")

    @transaction.atomic
    def handle(self, *args, **options):
        user_model = get_user_model()
        username = options["username"]
        email = options["email"]
        password = options["password"]
        company_name = options["company"]

        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": "Demo",
                "last_name": "Manager",
                "is_active": True,
                "is_staff": True,
            },
        )
        if created:
            user.set_password(password)
            user.save(update_fields=["password", "email", "first_name", "last_name", "is_active", "is_staff"])
        elif not user.is_active or not user.is_staff:
            user.is_active = True
            user.is_staff = True
            user.email = email
            user.save(update_fields=["is_active", "is_staff", "email"])

        organization, _ = Organization.objects.get_or_create(
            name=company_name,
            defaults={
                "owner": user,
                "plan": options["plan"],
                "subscription_status": "active",
            },
        )
        if organization.owner_id is None:
            organization.owner = user
        if organization.plan != options["plan"]:
            organization.plan = options["plan"]
        organization.subscription_status = "active"
        organization.save(update_fields=["owner", "plan", "subscription_status"])

        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "organization": organization,
                "full_name": "Demo Manager",
                "email_verified": True,
            },
        )
        if profile.organization_id != organization.pk:
            profile.organization = organization
        if not profile.full_name:
            profile.full_name = "Demo Manager"
        profile.email_verified = True
        profile.save(update_fields=["organization", "full_name", "email_verified"])

        categories = [
            "Electronics",
            "Office Supplies",
            "Packaging",
            "Safety",
            "Tools",
        ]
        category_map = {}
        for name in categories:
            category, _ = Category.objects.get_or_create(
                organization=organization,
                name=name,
            )
            category_map[name] = category

        supplier_data = [
            ("Kuba Parts", "+7 999 110-22-33", "parts@kubaparts.example"),
            ("Nordic Office", "+1 415 555-0102", "sales@nordicoffice.example"),
            ("SafePack Ltd.", "+44 20 7946 0958", "support@safepack.example"),
        ]
        supplier_map = {}
        for name, phone, email in supplier_data:
            supplier, _ = Supplier.objects.get_or_create(
                organization=organization,
                name=name,
                defaults={
                    "phone": phone,
                    "email": email,
                },
            )
            supplier.phone = phone
            supplier.email = email
            supplier.save(update_fields=["phone", "email"])
            supplier_map[name] = supplier

        products = [
            ("Barcode Scanner", "ELE-1001", "Electronics", "Kuba Parts", 58, Decimal("249.00"), 8, "Handheld barcode reader for fast stock checks."),
            ("Industrial Label Printer", "ELE-1002", "Electronics", "Kuba Parts", 24, Decimal("489.00"), 6, "Thermal label printer for warehouse operations."),
            ("Ergonomic Mouse", "OFF-2001", "Office Supplies", "Nordic Office", 72, Decimal("22.50"), 15, "Silent ergonomic mouse for operators."),
            ("Desk Organizer", "OFF-2002", "Office Supplies", "Nordic Office", 41, Decimal("35.00"), 10, "Modular organizer for desk tools and stationery."),
            ("Packing Tape", "PKG-3001", "Packaging", "SafePack Ltd.", 190, Decimal("12.90"), 30, "Industrial packaging tape for cartons and pallets."),
            ("Bubble Wrap Roll", "PKG-3002", "Packaging", "SafePack Ltd.", 140, Decimal("18.40"), 24, "Protective wrap for friction-sensitive goods."),
            ("Safety Gloves", "SAFE-4001", "Safety", "SafePack Ltd.", 132, Decimal("8.75"), 18, "Reusable gloves for handling and packing."),
            ("Safety Helmet", "SAFE-4002", "Safety", "SafePack Ltd.", 67, Decimal("31.00"), 10, "High-impact helmet for warehouse safety."),
            ("Cordless Drill", "TOOL-5001", "Tools", "Kuba Parts", 19, Decimal("149.00"), 7, "Compact drill for installation and assembly."),
            ("Adjustable Wrench", "TOOL-5002", "Tools", "Kuba Parts", 46, Decimal("39.99"), 12, "Reliable wrench for equipment servicing."),
            ("A4 Notepad", "OFF-2003", "Office Supplies", "Nordic Office", 120, Decimal("5.20"), 25, "Notepad for notes and daily tasks."),
            ("Stapler Set", "OFF-2004", "Office Supplies", "Nordic Office", 52, Decimal("18.00"), 12, "Desk stapler and refill pack."),
            ("Poly Mailers", "PKG-3003", "Packaging", "SafePack Ltd.", 230, Decimal("9.50"), 40, "Plastic mailers for secure dispatch."),
            ("Reflective Vest", "SAFE-4003", "Safety", "SafePack Ltd.", 89, Decimal("26.00"), 12, "High-visibility vest for warehouse staff."),
            ("Power Bank", "ELE-1003", "Electronics", "Kuba Parts", 37, Decimal("64.90"), 10, "Portable power source for mobile devices and scanners."),
        ]

        for name, sku, category_name, supplier_name, quantity, unit_price, minimum_stock, description in products:
            Product.objects.update_or_create(
                organization=organization,
                sku=sku,
                defaults={
                    "name": name,
                    "category": category_map[category_name],
                    "supplier": supplier_map[supplier_name],
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "minimum_stock": minimum_stock,
                    "description": description,
                    "is_active": True,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data loaded: organization '{company_name}', login '{username}', password '{password}'"
            )
        )
