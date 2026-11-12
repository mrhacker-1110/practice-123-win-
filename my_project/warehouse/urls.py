from django.urls import path

from . import views

urlpatterns = [
    path("backup/export/", views.export_warehouse_backup, name="export_warehouse_backup"),
    path("backup/restore/", views.restore_warehouse_backup, name="restore_warehouse_backup"),
    path("", views.dashboard, name="dashboard"),
    path("inventory/advisor/", views.inventory_advisor, name="inventory_advisor"),
    path("assistant/", views.ai_consultant, name="ai_consultant"),
    path("assistant/chat/", views.ai_consultant_chat, name="ai_consultant_chat"),
    path("assistant/new/", views.new_ai_consultant_conversation, name="new_ai_consultant_conversation"),
    path("assistant/<int:pk>/delete/", views.delete_ai_consultant_conversation, name="delete_ai_consultant_conversation"),
    path("products/", views.product_list, name="products"),
    path(
        "products/import/template.csv",
        views.product_import_template,
        name="product_import_template",
    ),
    path("products/import/", views.product_import, name="product_import"),
    path("products/add/", views.product_create, name="product_create"),
    path("products/analyze-photo/", views.analyze_product_photo, name="analyze_product_photo"),
    path("products/<int:pk>/edit/", views.product_update, name="product_update"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
    path("movements/", views.stock_movements, name="stock_movements"),
    path("purchases/", views.purchase_list, name="purchases"),
    path("purchases/add/", views.purchase_create, name="purchase_create"),
    path("suppliers/", views.supplier_list, name="suppliers"),
    path("customers/", views.customer_list, name="customers"),
    path("sales/", views.sales_report, name="sales_report"),
    path("sales/add/", views.sale_create, name="sale_create"),
    path(
        "sales/export/excel/",
        views.export_sales_report_excel,
        name="export_sales_report_excel",
    ),
    path(
        "sales/export/pdf/",
        views.export_sales_report_pdf,
        name="export_sales_report_pdf",
    ),
    path("products/export.csv", views.export_products_csv, name="export_products_csv"),
]
