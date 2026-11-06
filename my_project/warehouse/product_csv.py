import csv
from decimal import Decimal, InvalidOperation
from io import StringIO

from .models import Category, Product

MAX_IMPORT_ROWS = 500
REQUIRED_COLUMNS = {"name", "sku", "quantity", "unit_price", "minimum_stock"}
COLUMN_ALIASES = {
    "название": "name",
    "артикул": "sku",
    "stock": "quantity",
    "остаток": "quantity",
    "количество": "quantity",
    "price": "unit_price",
    "цена": "unit_price",
    "минимум": "minimum_stock",
    "минимальный остаток": "minimum_stock",
    "категория": "category",
}


def parse_product_csv(upload, organization, language="en"):
    russian = language == "ru"
    if upload.size > 5 * 1024 * 1024:
        return [], [
            "Размер CSV-файла не должен превышать 5 МБ."
            if russian
            else "CSV file exceeds the 5 MB size limit."
        ]

    try:
        content = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [
            "CSV-файл должен быть в кодировке UTF-8."
            if russian
            else "CSV must use UTF-8 encoding."
        ]

    try:
        reader = csv.DictReader(StringIO(content))
        if not reader.fieldnames:
            return [], [
                "CSV-файл пуст или не содержит строки заголовков."
                if russian
                else "CSV file is empty or has no header row."
            ]

        columns = {}
        for header in reader.fieldnames:
            normalized = (header or "").strip().casefold().replace("_", " ")
            normalized = " ".join(normalized.split())
            canonical = COLUMN_ALIASES.get(normalized, normalized.replace(" ", "_"))
            if canonical in columns:
                return [], [
                    f"Повторяющийся столбец CSV: {header}."
                    if russian
                    else f"Duplicate CSV column: {header}."
                ]
            columns[canonical] = header

        missing_columns = REQUIRED_COLUMNS - columns.keys()
        if missing_columns:
            column_names = {
                "name": "Название",
                "sku": "Артикул",
                "quantity": "Количество",
                "unit_price": "Цена",
                "minimum_stock": "Минимум",
            }
            return [], [
                (
                    "Не хватает обязательных столбцов CSV: "
                    + ", ".join(column_names[column] for column in sorted(missing_columns))
                    + "."
                    if russian
                    else "Missing required CSV columns: "
                    + ", ".join(sorted(missing_columns))
                    + "."
                )
            ]

        existing_skus = set(
            Product.objects.filter(organization=organization).values_list("sku", flat=True)
        )
        category_by_name = {
            category.name.casefold(): category
            for category in Category.objects.filter(organization=organization)
        }
        seen_skus = set()
        products = []
        errors = []

        for row_number, raw_row in enumerate(reader, start=2):
            if row_number > MAX_IMPORT_ROWS + 1:
                errors.append(
                    f"В CSV допускается не более {MAX_IMPORT_ROWS} товаров."
                    if russian
                    else f"CSV file may contain at most {MAX_IMPORT_ROWS} products."
                )
                break
            if None in raw_row:
                errors.append(
                    f"Строка {row_number}: значений больше, чем столбцов в заголовке."
                    if russian
                    else f"Row {row_number}: too many values for the header."
                )
                continue

            row = {
                column: (raw_row.get(source) or "").strip()
                for column, source in columns.items()
            }
            if not any(row.values()):
                continue

            row_errors = []
            name, sku = row["name"], row["sku"]
            if not name or len(name) > 150:
                row_errors.append(
                    "название обязательно и должно содержать не более 150 символов"
                    if russian
                    else "name is required and must be at most 150 characters"
                )
            if not sku or len(sku) > 50:
                row_errors.append(
                    "артикул обязателен и должен содержать не более 50 символов"
                    if russian
                    else "SKU is required and must be at most 50 characters"
                )
            elif sku in existing_skus:
                row_errors.append(
                    "артикул уже существует в этой рабочей области"
                    if russian
                    else "SKU already exists in this workspace"
                )
            elif sku in seen_skus:
                row_errors.append(
                    "артикул повторяется в файле"
                    if russian
                    else "SKU is duplicated in this file"
                )
            else:
                seen_skus.add(sku)

            try:
                quantity = int(row["quantity"])
                if quantity < 0:
                    raise ValueError
            except ValueError:
                quantity = 0
                row_errors.append(
                    "количество должно быть целым неотрицательным числом"
                    if russian
                    else "quantity must be a non-negative whole number"
                )

            try:
                unit_price = Decimal(row["unit_price"])
                if (
                    not unit_price.is_finite()
                    or unit_price < 0
                    or unit_price.as_tuple().exponent < -2
                    or unit_price > Decimal("99999999.99")
                ):
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                unit_price = Decimal("0.00")
                row_errors.append(
                    "цена должна быть от 0 до 99999999.99 и содержать не более 2 знаков после запятой"
                    if russian
                    else "unit price must be between 0 and 99999999.99 with at most 2 decimals"
                )

            try:
                minimum_stock = int(row["minimum_stock"])
                if minimum_stock < 0:
                    raise ValueError
            except ValueError:
                minimum_stock = 0
                row_errors.append(
                    "минимальный остаток должен быть целым неотрицательным числом"
                    if russian
                    else "minimum stock must be a non-negative whole number"
                )

            category_name = row.get("category", "")
            category = None
            if category_name:
                category = category_by_name.get(category_name.casefold())
                if category is None:
                    row_errors.append(
                        f"категория «{category_name}» не найдена в этой рабочей области"
                        if russian
                        else f"category '{category_name}' does not exist in this workspace"
                    )

            if row_errors:
                if len(errors) < 30:
                    errors.append(
                        f"Строка {row_number}: "
                        + "; ".join(row_errors)
                        + "."
                        if russian
                        else f"Row {row_number}: " + "; ".join(row_errors) + "."
                    )
                continue

            products.append(
                {
                    "name": name,
                    "sku": sku,
                    "quantity": quantity,
                    "unit_price": str(unit_price),
                    "minimum_stock": minimum_stock,
                    "category_id": category.pk if category else None,
                    "category_name": category.name if category else "",
                }
            )

        if not products and not errors:
            errors.append(
                "В CSV нет строк с товарами."
                if russian
                else "The CSV contains no product rows."
            )
        return products, errors
    except csv.Error as error:
        return [], [
            f"Не удалось прочитать CSV: {error}"
            if russian
            else f"Unable to read CSV: {error}"
        ]
