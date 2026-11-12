import json
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Category, Customer, Product, Purchase, Sale, StockMovement, Supplier

BACKUP_FORMAT = "warehouse-pro-backup"
BACKUP_VERSION = 1
MAX_ROWS_PER_TABLE = 20000
TABLE_MODELS = {
    "categories": Category,
    "suppliers": Supplier,
    "customers": Customer,
    "products": Product,
    "stock_movements": StockMovement,
    "sales": Sale,
    "purchases": Purchase,
}


def _dt(value):
    return value.isoformat() if value else None


def build_backup(organization):
    products = Product.objects.filter(organization=organization).order_by("pk")
    payload = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": timezone.now().isoformat(),
        "organization": {"name": organization.name},
        "records": {
            "categories": list(Category.objects.filter(organization=organization).order_by("pk").values("id", "name")),
            "suppliers": list(Supplier.objects.filter(organization=organization).order_by("pk").values("id", "name", "phone", "email", "company")),
            "customers": [
                {"id": row.pk, "name": row.name, "phone": row.phone, "email": row.email, "company": row.company, "created_at": _dt(row.created_at)}
                for row in Customer.objects.filter(organization=organization).order_by("pk")
            ],
            "products": [
                {"id": row.pk, "name": row.name, "sku": row.sku, "category_id": row.category_id, "supplier_id": row.supplier_id, "quantity": row.quantity, "unit_price": str(row.unit_price), "minimum_stock": row.minimum_stock, "description": row.description, "is_active": row.is_active, "created_at": _dt(row.created_at), "updated_at": _dt(row.updated_at)}
                for row in products
            ],
            "stock_movements": [
                {"id": row.pk, "product_id": row.product_id, "movement_type": row.movement_type, "quantity": row.quantity, "notes": row.notes, "created_at": _dt(row.created_at)}
                for row in StockMovement.objects.filter(organization=organization).order_by("pk")
            ],
            "sales": [
                {"id": row.pk, "product_id": row.product_id, "customer_id": row.customer_id, "quantity": row.quantity, "unit_price": str(row.unit_price), "total_price": str(row.total_price), "sold_at": _dt(row.sold_at)}
                for row in Sale.objects.filter(organization=organization).order_by("pk")
            ],
            "purchases": [
                {"id": row.pk, "product_id": row.product_id, "supplier_id": row.supplier_id, "quantity": row.quantity, "unit_cost": str(row.unit_cost), "total_cost": str(row.total_cost), "reference": row.reference, "received_at": _dt(row.received_at)}
                for row in Purchase.objects.filter(organization=organization).order_by("pk")
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _rows(records, key):
    value = records.get(key)
    if not isinstance(value, list) or len(value) > MAX_ROWS_PER_TABLE:
        raise ValidationError(f"Invalid {key} list.")
    if any(not isinstance(row, dict) for row in value):
        raise ValidationError(f"Invalid row in {key}.")
    return value


def _time(row, key):
    value = row.get(key)
    if not isinstance(value, str):
        raise ValidationError(f"Missing {key} timestamp.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid {key} timestamp.") from exc
    return timezone.make_aware(result) if timezone.is_naive(result) else result


def _id(value, optional=False):
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError("Invalid record reference.")
    return value


def _decimal(row, key):
    try:
        value = Decimal(str(row[key]))
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise ValidationError(f"Invalid {key} amount.") from exc
    if not value.is_finite():
        raise ValidationError(f"Invalid {key} amount.")
    return value


def _save_timestamp(model, pk, field, value):
    model.objects.filter(pk=pk).update(**{field: value})


@transaction.atomic
def restore_backup(organization, payload):
    if not isinstance(payload, dict) or payload.get("format") != BACKUP_FORMAT or payload.get("version") != BACKUP_VERSION:
        raise ValidationError("Unsupported backup format or version.")
    records = payload.get("records")
    if not isinstance(records, dict) or set(records) != set(TABLE_MODELS):
        raise ValidationError("Backup is missing one or more data tables.")
    rows = {key: _rows(records, key) for key in TABLE_MODELS}
    if sum(map(len, rows.values())) > MAX_ROWS_PER_TABLE:
        raise ValidationError("Backup contains too many records.")

    # Remove dependent records first. The transaction rolls the deletion back if import fails.
    Sale.objects.filter(organization=organization).delete()
    Purchase.objects.filter(organization=organization).delete()
    StockMovement.objects.filter(organization=organization).delete()
    Product.objects.filter(organization=organization).delete()
    Category.objects.filter(organization=organization).delete()
    Supplier.objects.filter(organization=organization).delete()
    Customer.objects.filter(organization=organization).delete()

    maps = {key: {} for key in TABLE_MODELS}
    for key, model, fields in (
        ("categories", Category, ("name",)),
        ("suppliers", Supplier, ("name", "phone", "email", "company")),
        ("customers", Customer, ("name", "phone", "email", "company")),
    ):
        for row in rows[key]:
            old_id = _id(row.get("id"))
            values = {field: row.get(field, "") for field in fields}
            instance = model.objects.create(organization=organization, **values)
            maps[key][old_id] = instance
            if key == "customers":
                _save_timestamp(model, instance.pk, "created_at", _time(row, "created_at"))

    for row in rows["products"]:
        old_id = _id(row.get("id"))
        category_id = _id(row.get("category_id"), optional=True)
        supplier_id = _id(row.get("supplier_id"), optional=True)
        category = maps["categories"].get(category_id) if category_id else None
        supplier = maps["suppliers"].get(supplier_id) if supplier_id else None
        if (category_id and category is None) or (supplier_id and supplier is None):
            raise ValidationError("Product refers to a missing category or supplier.")
        instance = Product(
            organization=organization,
            name=row.get("name", ""),
            sku=row.get("sku", ""),
            category=category,
            supplier=supplier,
            quantity=row.get("quantity"),
            unit_price=_decimal(row, "unit_price"),
            minimum_stock=row.get("minimum_stock"),
            description=row.get("description", ""),
            is_active=row.get("is_active", True),
        )
        instance.full_clean()
        instance.save()
        maps["products"][old_id] = instance
        _save_timestamp(Product, instance.pk, "created_at", _time(row, "created_at"))
        _save_timestamp(Product, instance.pk, "updated_at", _time(row, "updated_at"))

    for row in rows["stock_movements"]:
        product_id = _id(row.get("product_id"))
        product = maps["products"].get(product_id)
        if product is None:
            raise ValidationError("Stock movement refers to a missing product.")
        instance = StockMovement.objects.create(
            organization=organization, product=product,
            movement_type=row.get("movement_type"), quantity=row.get("quantity"),
            notes=row.get("notes", ""), created_by=None,
        )
        instance.full_clean()
        _save_timestamp(StockMovement, instance.pk, "created_at", _time(row, "created_at"))

    for key, model, time_field, value_field in (
        ("sales", Sale, "sold_at", "total_price"),
        ("purchases", Purchase, "received_at", "total_cost"),
    ):
        for row in rows[key]:
            product = maps["products"].get(_id(row.get("product_id")))
            if product is None:
                raise ValidationError(f"{key} refers to a missing product.")
            common = {
                "organization": organization,
                "product": product,
                "quantity": row.get("quantity"),
                "created_by": None,
            }
            if key == "sales":
                customer_id = _id(row.get("customer_id"), optional=True)
                customer = maps["customers"].get(customer_id) if customer_id else None
                if customer_id and customer is None:
                    raise ValidationError("Sale refers to a missing customer.")
                common.update(customer=customer,
                              unit_price=_decimal(row, "unit_price"), total_price=_decimal(row, value_field))
            else:
                supplier_id = _id(row.get("supplier_id"), optional=True)
                supplier = maps["suppliers"].get(supplier_id) if supplier_id else None
                if supplier_id and supplier is None:
                    raise ValidationError("Purchase refers to a missing supplier.")
                common.update(
                    supplier=supplier,
                    unit_cost=_decimal(row, "unit_cost"),
                    total_cost=_decimal(row, value_field),
                    reference=row.get("reference", ""),
                )
            instance = model(**common)
            instance.full_clean()
            instance.save()
            _save_timestamp(model, instance.pk, time_field, _time(row, time_field))

    return {key: len(value) for key, value in rows.items()}