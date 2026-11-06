PLANS = {
    "free": {
        "icon": "🌱",
        "name": {"en": "Free", "ru": "Бесплатный"},
        "price": "0.00",
        "product_limit": 100,
        "report_months": 1,
        "features": {
            "en": [
                "Up to 100 active products",
                "Inventory, purchases and sales",
                "Russian and English interface",
                "One-month sales summary",
                "Inventory dashboard and basic stock alerts",
            ],
            "ru": [
                "До 100 активных товаров",
                "Склад, поставки и продажи",
                "Интерфейс на русском и английском",
                "Отчёт за один месяц",
                "Панель склада и базовые сигналы о низком остатке",
            ],
        },
    },
    "pro": {
        "icon": "🚀",
        "name": {"en": "Pro", "ru": "Про"},
        "price": "19.00",
        "product_limit": 2000,
        "report_months": 6,
        "features": {
            "en": [
                "Up to 2,000 active products",
                "CSV product export",
                "CSV product import with preview and validation",
                "Excel and PDF sales report exports",
                "Sales analytics with six-month report history",
            ],
            "ru": [
                "До 2 000 активных товаров",
                "Экспорт товаров в CSV",
                "Импорт товаров CSV с проверкой и предпросмотром",
                "Экспорт отчётов о продажах в Excel и PDF",
                "Аналитика продаж и история отчётов за 6 месяцев",
            ],
        },
    },
    "super": {
        "icon": "👑",
        "name": {"en": "Super", "ru": "Супер"},
        "price": "49.00",
        "product_limit": 20000,
        "report_months": 36,
        "features": {
            "en": [
                "Up to 20,000 active products",
                "CSV product export",
                "CSV product import with preview and validation",
                "Excel and PDF sales report exports",
                "Thirty-six-month sales analytics",
                "Local inventory advisor using recent sales and stock levels",
                "Reorder recommendations with a transparent 14-day target",
            ],
            "ru": [
                "До 20 000 активных товаров",
                "Экспорт товаров в CSV",
                "Импорт товаров CSV с проверкой и предпросмотром",
                "Экспорт отчётов о продажах в Excel и PDF",
                "Аналитика продаж за 36 месяцев",
                "Локальный помощник по остаткам и недавним продажам",
                "Рекомендации к закупке с понятным запасом на 14 дней",
            ],
        },
    },
}


def has_paid_plan(organization):
    return organization.plan in {"pro", "super"} and organization.subscription_status == "active"


def effective_plan(organization):
    if organization.plan != "free" and not has_paid_plan(organization):
        return "free"
    return organization.plan


def effective_plan_for_user(organization, user):
    if user.is_superuser:
        return "super"
    return effective_plan(organization)
