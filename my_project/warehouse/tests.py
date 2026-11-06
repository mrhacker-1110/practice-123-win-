import json
import smtplib
from io import BytesIO
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import (
    Category,
    Customer,
    Organization,
    Product,
    Purchase,
    Sale,
    StockMovement,
    Supplier,
    UserProfile,
)
from warehouse_project.email_backend import UnicodeSafeConsoleEmailBackend
from .forms import TERMS_VERSION
from .product_csv import parse_product_csv


class Cp1252TestStream:
    encoding = "cp1252"

    def __init__(self):
        self.content = []

    def write(self, text):
        text.encode(self.encoding)
        self.content.append(text)

    def flush(self):
        pass


class WarehouseWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="warehouse-user",
            password="safe-test-password",
            is_staff=True,
        )
        self.organization = Organization.objects.create(
            name="Warehouse Co",
            owner=self.user,
            plan="pro",
            subscription_status="active",
        )
        UserProfile.objects.create(
            user=self.user,
            organization=self.organization,
            email_verified=True,
        )
        self.category = Category.objects.create(name="Tools", organization=self.organization)
        self.supplier = Supplier.objects.create(name="Supply Co", organization=self.organization)
        self.customer = Customer.objects.create(name="Customer One", organization=self.organization)
        self.product = Product.objects.create(
            organization=self.organization,
            name="Hammer",
            sku="TOOL-1",
            category=self.category,
            supplier=self.supplier,
            quantity=10,
            unit_price=Decimal("12.50"),
            minimum_stock=2,
        )
        self.client.force_login(self.user)

    def test_inventory_pages_require_login(self):
        self.client.logout()

        response = self.client.get(reverse("products"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_non_staff_account_cannot_access_inventory(self):
        user = get_user_model().objects.create_user(
            username="regular-user",
            password="safe-test-password",
        )
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("products")).status_code, 403)

    def test_login_page_and_authentication(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

        response = self.client.post(
            reverse("login"),
            {"username": "warehouse-user", "password": "safe-test-password"},
        )

        self.assertRedirects(response, reverse("dashboard"))

    def test_main_pages_render_for_authenticated_user(self):
        urls = [
            "dashboard",
            "products",
            "product_import",
            "inventory_advisor",
            "stock_movements",
            "purchases",
            "purchase_create",
            "suppliers",
            "customers",
            "sales_report",
            "sale_create",
            "product_create",
            "profile",
            "plans",
        ]

        for name in urls:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_mobile_navigation_themes_and_legal_links_render(self):
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, 'class="nav-toggle"')
        self.assertContains(response, 'id="theme-select"')
        self.assertContains(response, "System")
        self.assertContains(response, "Light")
        self.assertContains(response, "Dark")
        self.assertContains(response, reverse("legal_information", args=["terms"]))
        self.assertContains(response, reverse("legal_information", args=["privacy"]))

    def test_dashboard_tables_use_contained_scroll_wrappers(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="dashboard-data-table"', count=2)

    def test_registration_form_exposes_live_validation_and_required_consent(self):
        self.client.logout()
        response = self.client.get(reverse("register"))

        self.assertContains(response, "data-live-validation")
        self.assertContains(response, 'id="id_accept_terms"')
        self.assertContains(response, 'name="accept_terms" class="consent-checkbox" required')
        self.assertContains(response, 'minlength="8"')
        self.assertContains(response, reverse("legal_information", args=["terms"]))
        self.assertContains(response, reverse("legal_information", args=["privacy"]))
        self.assertNotContains(response, reverse("resend_verification"))
        self.assertContains(response, "data-submitting-label=")

    def test_registration_validation_errors_are_visible_and_focusable(self):
        self.client.logout()
        response = self.client.post(
            reverse("register"),
            {
                "username": "invalid-owner",
                "email": "invalid-owner@example.com",
                "company_name": "Invalid Company",
                "password1": "short",
                "password2": "different",
                "accept_terms": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="message error-message form-error-summary"')
        self.assertContains(response, 'role="alert" id="id_password2-errors"')
        self.assertFalse(get_user_model().objects.filter(username="invalid-owner").exists())

    def test_registration_sends_russian_confirmation_on_cp1252_console(self):
        self.client.logout()
        self.client.post(
            reverse("set_language"),
            {"language": "ru", "next": reverse("register")},
        )
        stream = Cp1252TestStream()
        backend = UnicodeSafeConsoleEmailBackend(stream=stream)
        message = EmailMultiAlternatives(
            "Подтвердите адрес электронной почты",
            "Здравствуйте!\nПодтвердите адрес: http://testserver/accounts/verify/example/token/",
            "no-reply@example.com",
            ["new-owner@example.com"],
            connection=backend,
        )

        self.assertEqual(backend.send_messages([message]), 1)
        output = "".join(stream.content)
        self.assertIn(r"\u0417", output)
        self.assertIn("/accounts/verify/example/token/", output)

    @override_settings(
        EMAIL_BACKEND="warehouse_project.email_backend.UnicodeSafeConsoleEmailBackend"
    )
    def test_russian_registration_completes_on_cp1252_console(self):
        self.client.logout()
        self.client.post(
            reverse("set_language"),
            {"language": "ru", "next": reverse("register")},
        )
        stream = Cp1252TestStream()

        with patch("sys.stdout", stream):
            response = self.client.post(
                reverse("register"),
                {
                    "username": "russian-console-owner",
                    "email": "russian-console@example.com",
                    "company_name": "Русская компания",
                    "password1": "N!ceSecurePassword229",
                    "password2": "N!ceSecurePassword229",
                    "accept_terms": "on",
                },
            )

        self.assertRedirects(response, reverse("registration_complete"))
        user = get_user_model().objects.get(username="russian-console-owner")
        self.assertFalse(user.is_active)
        self.assertFalse(user.warehouse_profile.email_verified)
        console_output = "".join(stream.content)
        self.assertIn(r"\u0417", console_output)
        self.assertIn("/accounts/verify/", console_output)

    def test_legal_documents_render_in_both_languages(self):
        terms = self.client.get(reverse("legal_information", args=["terms"]))
        privacy = self.client.get(reverse("legal_information", args=["privacy"]))
        self.assertContains(terms, "Terms of Service")
        self.assertContains(privacy, "Privacy Notice")
        self.assertEqual(self.client.get(reverse("legal_information", args=["unknown"])).status_code, 404)

        self.client.post(
            reverse("set_language"),
            {"language": "ru", "next": reverse("dashboard")},
        )
        localized_terms = self.client.get(reverse("legal_information", args=["terms"]))
        self.assertContains(localized_terms, "Пользовательское соглашение")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_registration_requires_consent_before_creating_an_account(self):
        self.client.logout()
        response = self.client.post(
            reverse("register"),
            {
                "username": "consent-owner",
                "email": "consent-owner@example.com",
                "first_name": "Consent",
                "last_name": "Owner",
                "company_name": "Consent Company",
                "password1": "nV!7uQ4#pR9zT2xK",
                "password2": "nV!7uQ4#pR9zT2xK",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].has_error("accept_terms"))
        self.assertFalse(get_user_model().objects.filter(username="consent-owner").exists())
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch("warehouse.views.send_mail", side_effect=smtplib.SMTPException("mail offline"))
    def test_email_delivery_failure_explains_account_exists_and_resend_path(self, send_mail_mock):
        self.client.logout()

        with self.assertLogs("warehouse.views", level="ERROR"):
            response = self.client.post(
                reverse("register"),
                {
                    "username": "mail-offline-owner",
                    "email": "mail-offline@example.com",
                    "company_name": "Mail Offline Company",
                    "password1": "N!ceSecurePassword228",
                    "password2": "N!ceSecurePassword228",
                    "accept_terms": "on",
                },
                follow=True,
            )

        self.assertEqual(response.resolver_match.url_name, "resend_verification")
        user = get_user_model().objects.get(username="mail-offline-owner")
        self.assertFalse(user.is_active)
        self.assertFalse(user.warehouse_profile.email_verified)
        self.assertContains(response, "Your account was created, but we could not send the email")
        send_mail_mock.assert_called_once()

    def test_administrator_can_view_all_accounts_profiles_and_workspaces(self):
        admin_user = get_user_model().objects.create_superuser(
            username="site-admin",
            email="admin@example.com",
            password="Admin-Password-928!",
        )
        self.client.force_login(admin_user)

        admin_pages = (
            "admin:auth_user_changelist",
            "admin:warehouse_userprofile_changelist",
            "admin:warehouse_organization_changelist",
        )
        for page_name in admin_pages:
            with self.subTest(page=page_name):
                self.assertEqual(self.client.get(reverse(page_name)).status_code, 200)

        profile_page = self.client.get(reverse("admin:warehouse_userprofile_changelist"))
        self.assertContains(profile_page, "warehouse-user")
        organization_page = self.client.get(reverse("admin:warehouse_organization_changelist"))
        self.assertContains(organization_page, "Warehouse Co")
        delete_response = self.client.post(
            reverse(
                "admin:warehouse_organization_delete",
                args=[self.organization.pk],
            )
        )
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(Organization.objects.filter(pk=self.organization.pk).exists())
        protected_admin_deletions = (
            ("admin:auth_user_delete", self.user.pk),
            ("admin:warehouse_userprofile_delete", self.user.warehouse_profile.pk),
            ("admin:warehouse_product_delete", self.product.pk),
            ("admin:warehouse_category_delete", self.category.pk),
            ("admin:warehouse_supplier_delete", self.supplier.pk),
            ("admin:warehouse_customer_delete", self.customer.pk),
        )
        for page_name, record_id in protected_admin_deletions:
            with self.subTest(page=page_name):
                response = self.client.post(reverse(page_name, args=[record_id]))
                self.assertEqual(response.status_code, 403)

    def test_regular_staff_user_cannot_view_sitewide_account_records(self):
        response = self.client.get(reverse("admin:auth_user_changelist"))

        self.assertEqual(response.status_code, 403)

    def test_dashboard_chart_shows_organization_inventory_by_category(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(
            response.context["inventory_chart"],
            [{"label": "Tools", "quantity": 10, "width": 100, "color": "blue"}],
        )
        self.assertContains(response, "Stock by category")

    def test_sale_updates_stock_and_creates_auditable_movement(self):
        response = self.client.post(
            reverse("sale_create"),
            {"product": self.product.pk, "customer": self.customer.pk, "quantity": 3},
        )

        self.assertRedirects(response, reverse("sales_report"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
        self.assertEqual(Sale.objects.get().total_price, Decimal("37.50"))
        self.assertEqual(Sale.objects.get().created_by, self.user)
        movement = StockMovement.objects.get()
        self.assertEqual((movement.movement_type, movement.quantity), ("out", 3))
        self.assertEqual(movement.created_by, self.user)
        report = self.client.get(reverse("sales_report"))
        self.assertContains(report, "$37.50")
        chart_data = report.context["chart_data"]
        self.assertEqual(chart_data[-1]["revenue"], Decimal("37.50"))
        self.assertEqual(chart_data[0]["revenue"], Decimal("0.00"))

    def test_sale_cannot_make_stock_negative(self):
        response = self.client.post(
            reverse("sale_create"),
            {"product": self.product.pk, "customer": "", "quantity": 11},
        )

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_sales_report_window_matches_selected_plan(self):
        sale = Sale.objects.create(
            product=self.product,
            organization=self.organization,
            customer=self.customer,
            quantity=1,
            unit_price=self.product.unit_price,
            total_price=self.product.unit_price,
        )
        Sale.objects.filter(pk=sale.pk).update(
            sold_at=timezone.now() - timedelta(days=70)
        )

        self.organization.plan = "free"
        self.organization.subscription_status = "free"
        self.organization.save(update_fields=["plan", "subscription_status"])
        free_report = self.client.get(reverse("sales_report"))
        self.assertEqual(free_report.context["page_obj"].paginator.count, 0)
        self.assertEqual(len(free_report.context["chart_data"]), 1)

        self.organization.plan = "pro"
        self.organization.subscription_status = "active"
        self.organization.save(update_fields=["plan", "subscription_status"])
        pro_report = self.client.get(reverse("sales_report"))
        self.assertEqual(pro_report.context["page_obj"].paginator.count, 1)
        self.assertEqual(len(pro_report.context["chart_data"]), 6)

        self.organization.plan = "super"
        self.organization.save(update_fields=["plan"])
        super_report = self.client.get(reverse("sales_report"))
        self.assertEqual(super_report.context["page_obj"].paginator.count, 1)
        self.assertEqual(len(super_report.context["chart_data"]), 36)

    def test_sales_report_custom_date_range_filters_sales_and_chart(self):
        old_sale = Sale.objects.create(
            product=self.product,
            organization=self.organization,
            quantity=1,
            unit_price=Decimal("12.50"),
            total_price=Decimal("12.50"),
        )
        Sale.objects.filter(pk=old_sale.pk).update(
            sold_at=timezone.now() - timedelta(days=70)
        )
        current_sale = Sale.objects.create(
            product=self.product,
            organization=self.organization,
            quantity=1,
            unit_price=Decimal("12.50"),
            total_price=Decimal("12.50"),
        )
        start_date = timezone.localdate() - timedelta(days=90)
        end_date = timezone.localdate() - timedelta(days=60)

        response = self.client.get(
            reverse("sales_report"),
            {
                "period": "custom",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["total_revenue"], Decimal("12.50"))
        self.assertEqual(len(response.context["chart_data"]), 31)
        self.assertEqual(
            response.context["page_obj"].object_list[0].pk,
            old_sale.pk,
        )
        self.assertNotEqual(
            response.context["page_obj"].object_list[0].pk,
            current_sale.pk,
        )

    def test_sales_report_exports_filtered_excel_and_pdf_without_cross_workspace_data(self):
        self.product.name = "=2+2"
        self.product.save(update_fields=["name"])
        included_sale = Sale.objects.create(
            product=self.product,
            organization=self.organization,
            quantity=2,
            unit_price=Decimal("12.50"),
            total_price=Decimal("25.00"),
            created_by=self.user,
        )
        Sale.objects.filter(pk=included_sale.pk).update(
            sold_at=timezone.now() - timedelta(days=70)
        )
        other_organization = Organization.objects.create(
            name="Other report workspace",
            owner=self.user,
            plan="pro",
            subscription_status="active",
        )
        other_product = Product.objects.create(
            organization=other_organization,
            name="Private workspace item",
            sku="PRIVATE-REPORT",
            quantity=1,
            unit_price=Decimal("1.00"),
        )
        Sale.objects.create(
            product=other_product,
            organization=other_organization,
            quantity=1,
            unit_price=Decimal("1.00"),
            total_price=Decimal("1.00"),
        )
        start_date = (timezone.localdate() - timedelta(days=90)).isoformat()
        end_date = (timezone.localdate() - timedelta(days=60)).isoformat()
        export_params = {
            "period": "custom",
            "start_date": start_date,
            "end_date": end_date,
        }

        excel = self.client.get(
            reverse("export_sales_report_excel"),
            export_params,
        )
        self.assertEqual(excel.status_code, 200)
        self.assertEqual(
            excel["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        workbook = load_workbook(BytesIO(excel.content), read_only=True, data_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
        workbook.close()
        self.assertEqual(rows[1][1], "'=2+2")
        self.assertNotIn("Private workspace item", str(rows))
        self.assertEqual(rows[-1][6], 25)

        pdf = self.client.get(reverse("export_sales_report_pdf"), export_params)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))

    def test_free_plan_cannot_export_sales_reports_and_invalid_dates_show_errors(self):
        self.organization.plan = "free"
        self.organization.subscription_status = "free"
        self.organization.save(update_fields=["plan", "subscription_status"])

        excel = self.client.get(
            reverse("export_sales_report_excel"),
            {"period": "plan"},
        )
        self.assertRedirects(excel, reverse("plan_detail", args=["pro"]))

        invalid = self.client.get(
            reverse("sales_report"),
            {
                "period": "custom",
                "start_date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "end_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            },
        )
        self.assertContains(invalid, "Reports cannot include future dates")
        self.assertEqual(invalid.context["page_obj"].paginator.count, 0)

    def test_purchase_adds_stock_and_movement(self):
        response = self.client.post(
            reverse("purchase_create"),
            {
                "product": self.product.pk,
                "supplier": self.supplier.pk,
                "quantity": 5,
                "unit_cost": "8.25",
                "reference": "PO-15",
            },
        )

        self.assertRedirects(response, reverse("purchases"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)
        self.assertEqual(Purchase.objects.get().total_cost, Decimal("41.25"))
        self.assertEqual(Purchase.objects.get().created_by, self.user)
        self.assertEqual(StockMovement.objects.get().movement_type, "in")

    def test_adjustment_sets_absolute_counted_stock(self):
        response = self.client.post(
            reverse("stock_movements"),
            {
                "product": self.product.pk,
                "movement_type": "adjust",
                "quantity": 4,
                "notes": "Physical count",
            },
        )

        self.assertRedirects(response, reverse("stock_movements"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 4)
        self.assertEqual(StockMovement.objects.get().quantity, 4)

    def test_category_filter_and_csv_export(self):
        response = self.client.get(reverse("products"), {"category": self.category.pk})
        self.assertContains(response, self.product.name)
        self.assertEqual(self.client.get(reverse("export_products_csv")).status_code, 200)

    def test_product_list_paginates_and_preserves_filters(self):
        Product.objects.bulk_create(
            [
                Product(
                    name=f"Box {index}",
                    sku=f"BOX-{index}",
                    category=self.category,
                    organization=self.organization,
                    unit_price=Decimal("1.00"),
                )
                for index in range(51)
            ]
        )

        response = self.client.get(reverse("products"), {"q": "Box", "page": 1})

        self.assertEqual(response.context["page_obj"].paginator.count, 51)
        self.assertContains(response, "?q=Box&amp;page=2")

    def test_archiving_preserves_transaction_history(self):
        sale = Sale.objects.create(
            product=self.product,
            organization=self.organization,
            customer=self.customer,
            quantity=1,
            unit_price=self.product.unit_price,
            total_price=self.product.unit_price,
        )

        response = self.client.post(reverse("product_delete", args=[self.product.pk]))

        self.assertRedirects(response, reverse("products"))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())

    def test_product_hard_delete_is_blocked_when_history_exists(self):
        sale = Sale.objects.create(
            product=self.product,
            organization=self.organization,
            customer=self.customer,
            quantity=1,
            unit_price=self.product.unit_price,
            total_price=self.product.unit_price,
        )
        movement = StockMovement.objects.create(
            product=self.product,
            organization=self.organization,
            movement_type="out",
            quantity=1,
        )

        with self.assertRaises(ProtectedError):
            self.product.delete()

        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())
        self.assertTrue(StockMovement.objects.filter(pk=movement.pk).exists())

    def test_workspace_hard_delete_is_blocked_when_it_contains_data(self):
        with self.assertRaises(ProtectedError):
            self.organization.delete()

        self.assertTrue(Organization.objects.filter(pk=self.organization.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(UserProfile.objects.filter(user=self.user).exists())

    def test_account_hard_delete_is_blocked_while_it_owns_a_workspace(self):
        with self.assertRaises(ProtectedError):
            self.user.delete()

        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=self.organization.pk).exists())

    def test_referenced_master_data_cannot_be_hard_deleted(self):
        Sale.objects.create(
            product=self.product,
            organization=self.organization,
            customer=self.customer,
            quantity=1,
            unit_price=self.product.unit_price,
            total_price=self.product.unit_price,
        )

        for record in (self.category, self.supplier, self.customer):
            with self.subTest(record=record):
                with self.assertRaises(ProtectedError):
                    record.delete()

        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(Sale.objects.exists())

    def test_language_switch_translates_interface_and_form_labels(self):
        self.client.post(
            reverse("set_language"),
            {"language": "ru", "next": reverse("dashboard")},
        )

        dashboard = self.client.get(reverse("dashboard"))
        product_form = self.client.get(reverse("product_create"))

        self.assertContains(dashboard, "Главная")
        self.assertContains(product_form, "Название")

    def test_company_data_is_isolated_between_workspaces(self):
        other_user = get_user_model().objects.create_user(
            username="other-owner",
            password="safe-test-password",
            is_staff=True,
        )
        other_organization = Organization.objects.create(
            name="Other Company",
            owner=other_user,
        )
        UserProfile.objects.create(
            user=other_user,
            organization=other_organization,
            email_verified=True,
        )
        other_product = Product.objects.create(
            organization=other_organization,
            name="Secret item",
            sku="SECRET-1",
            quantity=9,
        )

        response = self.client.get(reverse("products"))

        self.assertContains(response, self.product.name)
        self.assertNotContains(response, other_product.name)
        self.assertEqual(
            self.client.get(reverse("product_update", args=[other_product.pk])).status_code,
            404,
        )
        sale_response = self.client.post(
            reverse("sale_create"),
            {"product": other_product.pk, "customer": "", "quantity": 1},
        )
        self.assertEqual(sale_response.status_code, 200)
        self.product.refresh_from_db()
        other_product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertEqual(other_product.quantity, 9)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_registration_requires_email_confirmation_before_access(self):
        self.client.logout()
        response = self.client.post(
            reverse("register"),
            {
                "username": "new-warehouse-owner",
                "email": "new-owner@example.com",
                "first_name": "New",
                "last_name": "Owner",
                "company_name": "New Company",
                "phone": "555-0150",
                "password1": "F!r7v3rified&Secure928",
                "password2": "F!r7v3rified&Secure928",
                "accept_terms": "on",
            },
        )

        self.assertRedirects(response, reverse("registration_complete"))
        registration_complete = self.client.get(response.url)
        self.assertContains(registration_complete, "check your spam folder")
        self.assertContains(
            registration_complete,
            reverse("resend_verification"),
        )
        self.assertNotContains(
            registration_complete,
            "name=\"email\"",
        )
        user = get_user_model().objects.get(username="new-warehouse-owner")
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.warehouse_profile.organization.name, "New Company")
        self.assertIsNotNone(user.warehouse_profile.terms_accepted_at)
        self.assertEqual(user.warehouse_profile.terms_version, TERMS_VERSION)
        self.assertEqual(len(mail.outbox), 1)

        confirmation_url = next(
            line for line in mail.outbox[0].body.splitlines() if "/accounts/verify/" in line
        )
        path = urlsplit(confirmation_url).path
        confirmation = self.client.get(path)

        self.assertEqual(confirmation.status_code, 200)
        user.refresh_from_db()
        user.warehouse_profile.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.warehouse_profile.email_verified)

    def test_profile_updates_only_company_profile_fields(self):
        response = self.client.post(
            reverse("profile"),
            {
                "full_name": "Warehouse Owner",
                "phone": "555-0111",
                "company_name": "Updated Warehouse",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        self.organization.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.organization.name, "Updated Warehouse")
        self.assertEqual(self.user.email, "")

    def test_free_plan_enforces_product_limit(self):
        self.organization.plan = "free"
        self.organization.subscription_status = "free"
        self.organization.save(update_fields=["plan", "subscription_status"])
        Product.objects.bulk_create(
            [
                Product(
                    organization=self.organization,
                    name=f"Product {index}",
                    sku=f"FREE-{index}",
                )
                for index in range(100)
            ]
        )

        response = self.client.get(reverse("product_create"))

        self.assertRedirects(response, reverse("plans"))
        export_response = self.client.get(reverse("export_products_csv"))
        self.assertRedirects(export_response, reverse("plans"))

    def test_site_superuser_gets_all_paid_features_without_company_payment(self):
        admin_user = get_user_model().objects.create_superuser(
            username="paid-feature-admin",
            email="paid-feature-admin@example.com",
            password="Admin-Password-928!",
        )
        UserProfile.objects.create(
            user=admin_user,
            organization=self.organization,
            email_verified=True,
        )
        self.organization.plan = "free"
        self.organization.subscription_status = "free"
        self.organization.save(update_fields=["plan", "subscription_status"])
        Product.objects.bulk_create(
            [
                Product(
                    organization=self.organization,
                    name=f"Admin product {index}",
                    sku=f"ADMIN-{index}",
                )
                for index in range(100)
            ]
        )
        self.client.force_login(admin_user)

        self.assertEqual(self.client.get(reverse("product_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("export_products_csv")).status_code, 200)
        report = self.client.get(reverse("sales_report"))
        self.assertEqual(report.context["report_months"], 36)
        self.assertEqual(len(report.context["chart_data"]), 36)

        plans_response = self.client.get(reverse("plans"))
        self.assertTrue(plans_response.context["is_admin"])
        self.assertEqual(
            next(
                plan["key"]
                for plan in plans_response.context["plan_cards"]
                if plan["is_current"]
            ),
            "super",
        )
        detail = self.client.get(reverse("plan_detail", args=["super"]))
        self.assertContains(detail, "All features are available to the site administrator")

    @override_settings(
        PAYPAL_CLIENT_ID="test-client",
        PAYPAL_CLIENT_SECRET="test-secret",
        PAYPAL_WEBHOOK_ID="test-webhook",
        PAYPAL_PRO_PLAN_ID="P-PRO",
        PAYPAL_SUPER_PLAN_ID="P-SUPER",
    )
    def test_plan_details_show_price_and_paypal_checkout_action(self):
        self.organization.plan = "free"
        self.organization.subscription_status = "free"
        self.organization.save(update_fields=["plan", "subscription_status"])

        response = self.client.get(reverse("plan_detail", args=["pro"]))

        self.assertContains(response, "$19.00")
        self.assertContains(response, "six-month report history")
        self.assertContains(response, reverse("start_paypal_subscription", args=["pro"]))
        self.assertContains(response, "Continue with PayPal")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_flow_uses_registered_email(self):
        self.user.email = "warehouse-owner@example.com"
        self.user.save(update_fields=["email"])
        response = self.client.post(
            reverse("password_reset"),
            {"email": self.user.email},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(
        PAYPAL_CLIENT_ID="test-client",
        PAYPAL_CLIENT_SECRET="test-secret",
        PAYPAL_WEBHOOK_ID="test-webhook",
        PAYPAL_PRO_PLAN_ID="P-PRO",
        PAYPAL_SUPER_PLAN_ID="P-SUPER",
    )
    @patch("warehouse.views.api_request")
    def test_paypal_checkout_is_server_created_and_waits_for_verified_webhook(
        self,
        paypal_api,
    ):
        self.organization.plan = "free"
        self.organization.subscription_status = "free"
        self.organization.save(update_fields=["plan", "subscription_status"])
        paypal_api.return_value = {
            "id": "I-SUBSCRIPTION-1",
            "links": [
                {"rel": "approve", "href": "https://www.sandbox.paypal.com/checkout"}
            ],
        }

        response = self.client.post(
            reverse("start_paypal_subscription", args=["pro"])
        )

        self.assertRedirects(
            response,
            "https://www.sandbox.paypal.com/checkout",
            fetch_redirect_response=False,
        )
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.subscription_status, "pending")
        self.assertEqual(self.organization.pending_plan, "pro")
        self.assertEqual(
            paypal_api.call_args.args[2]["custom_id"],
            str(self.organization.pk),
        )

    @override_settings(
        PAYPAL_CLIENT_ID="test-client",
        PAYPAL_CLIENT_SECRET="test-secret",
        PAYPAL_WEBHOOK_ID="test-webhook",
        PAYPAL_PRO_PLAN_ID="P-PRO",
        PAYPAL_SUPER_PLAN_ID="P-SUPER",
    )
    @patch("warehouse.views.api_request")
    @patch("warehouse.views.verify_webhook", return_value=True)
    def test_verified_paypal_webhook_activates_only_matching_company(
        self,
        verify_webhook_mock,
        paypal_api,
    ):
        self.organization.pending_plan = "pro"
        self.organization.pending_paypal_subscription_id = "I-SUBSCRIPTION-1"
        self.organization.subscription_status = "pending"
        self.organization.save(
            update_fields=[
                "pending_plan",
                "pending_paypal_subscription_id",
                "subscription_status",
            ]
        )
        paypal_api.return_value = {
            "id": "I-SUBSCRIPTION-1",
            "custom_id": str(self.organization.pk),
            "plan_id": "P-PRO",
            "status": "ACTIVE",
        }
        event = {
            "id": "WH-EVENT-1",
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {"id": "I-SUBSCRIPTION-1"},
        }

        response = self.client.post(
            reverse("paypal_webhook"),
            data=json.dumps(event),
            content_type="application/json",
        )
        duplicate_response = self.client.post(
            reverse("paypal_webhook"),
            data=json.dumps(event),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(duplicate_response.json(), {"status": "duplicate"})
        self.assertEqual(verify_webhook_mock.call_count, 2)
        paypal_api.assert_called_once()
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.plan, "pro")
        self.assertEqual(self.organization.subscription_status, "active")
        self.assertEqual(
            self.organization.paypal_subscription_id,
            "I-SUBSCRIPTION-1",
        )

    @patch("warehouse.views.verify_webhook", return_value=True)
    def test_paypal_cancellation_clears_a_pending_subscription(self, webhook_mock):
        self.organization.pending_plan = "pro"
        self.organization.pending_paypal_subscription_id = "I-PENDING-1"
        self.organization.subscription_status = "pending"
        self.organization.save(
            update_fields=[
                "pending_plan",
                "pending_paypal_subscription_id",
                "subscription_status",
            ]
        )
        event = {
            "id": "WH-CANCEL-1",
            "event_type": "BILLING.SUBSCRIPTION.CANCELLED",
            "resource": {"id": "I-PENDING-1"},
        }

        response = self.client.post(
            reverse("paypal_webhook"),
            data=json.dumps(event),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.plan, "free")
        self.assertEqual(self.organization.subscription_status, "cancelled")
        self.assertEqual(self.organization.pending_plan, "")
        self.assertIsNone(self.organization.pending_paypal_subscription_id)
        webhook_mock.assert_called_once()

    @patch("warehouse.views.verify_webhook")
    def test_paypal_webhook_rejects_non_object_json(self, webhook_mock):
        response = self.client.post(
            reverse("paypal_webhook"),
            data="[]",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        webhook_mock.assert_not_called()

    def test_csv_import_previews_then_imports_atomically_with_stock_history(self):
        uploaded_file = SimpleUploadedFile(
            "products.csv",
            (
                "Name,SKU,Category,Quantity,Unit price,Minimum stock\r\n"
                "New widget,NEW-1,Tools,6,2.50,2\r\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )
        preview = self.client.post(
            reverse("product_import"),
            {"action": "preview", "csv_file": uploaded_file},
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(len(preview.context["preview"]), 1)
        self.assertContains(preview, "Confirm import")
        self.assertFalse(Product.objects.filter(sku="NEW-1").exists())

        result = self.client.post(
            reverse("product_import"),
            {
                "action": "import",
                "preview_token": preview.context["preview_token"],
            },
        )

        self.assertRedirects(result, reverse("products"))
        imported = Product.objects.get(organization=self.organization, sku="NEW-1")
        self.assertEqual(imported.quantity, 6)
        self.assertEqual(imported.category, self.category)
        self.assertEqual(imported.unit_price, Decimal("2.50"))
        movement = StockMovement.objects.get(product=imported)
        self.assertEqual(movement.movement_type, "adjust")
        self.assertEqual(movement.quantity, 6)

    def test_invalid_csv_does_not_import_valid_rows_from_the_same_file(self):
        uploaded_file = SimpleUploadedFile(
            "products.csv",
            (
                "Name,SKU,Category,Quantity,Unit price,Minimum stock\r\n"
                "Valid widget,NEW-2,Tools,4,3.00,1\r\n"
                "Invalid widget,NEW-3,Tools,not-a-number,3.00,1\r\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("product_import"),
            {"action": "preview", "csv_file": uploaded_file},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["errors"])
        self.assertFalse(Product.objects.filter(sku__in=["NEW-2", "NEW-3"]).exists())
        self.assertNotContains(response, "name=\"preview_token\"")

    def test_free_plan_cannot_import_csv_but_site_superuser_can(self):
        self.organization.plan = "free"
        self.organization.subscription_status = "free"
        self.organization.save(update_fields=["plan", "subscription_status"])
        upload = SimpleUploadedFile(
            "products.csv",
            b"Name,SKU,Quantity,Unit price,Minimum stock\r\nWidget,FREE-1,1,1.00,0\r\n",
            content_type="text/csv",
        )

        locked = self.client.get(reverse("product_import"))
        denied = self.client.post(
            reverse("product_import"),
            {"action": "preview", "csv_file": upload},
        )
        self.assertContains(locked, "Choose plan")
        self.assertRedirects(denied, reverse("plan_detail", args=["pro"]))
        self.assertFalse(Product.objects.filter(sku="FREE-1").exists())

        admin_user = get_user_model().objects.create_superuser(
            username="import-admin",
            email="import-admin@example.com",
            password="admin-import-password",
        )
        UserProfile.objects.create(
            user=admin_user,
            organization=self.organization,
            email_verified=True,
        )
        self.client.force_login(admin_user)
        admin_upload = SimpleUploadedFile(
            "products.csv",
            b"Name,SKU,Quantity,Unit price,Minimum stock\r\nWidget,ADMIN-1,1,1.00,0\r\n",
            content_type="text/csv",
        )
        admin_preview = self.client.post(
            reverse("product_import"),
            {"action": "preview", "csv_file": admin_upload},
        )
        self.assertEqual(admin_preview.status_code, 200)
        self.assertEqual(len(admin_preview.context["preview"]), 1)

    def test_csv_import_rejects_categories_from_another_workspace(self):
        other_org = Organization.objects.create(
            name="Other Co",
            owner=self.user,
            plan="pro",
            subscription_status="active",
        )
        Category.objects.create(name="Private category", organization=other_org)
        uploaded_file = SimpleUploadedFile(
            "products.csv",
            (
                "Name,SKU,Category,Quantity,Unit price,Minimum stock\r\n"
                "Widget,OTHER-CAT,Private category,1,1.00,0\r\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("product_import"),
            {"action": "preview", "csv_file": uploaded_file},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["errors"])
        self.assertFalse(Product.objects.filter(sku="OTHER-CAT").exists())

    def test_csv_parser_accepts_utf8_russian_export_headers(self):
        uploaded_file = SimpleUploadedFile(
            "товары.csv",
            (
                "Название,Артикул,Категория,Количество,Цена,Минимум\r\n"
                "Кофе,COFFEE-2,Tools,25,9.90,5\r\n"
            ).encode("utf-8-sig"),
            content_type="text/csv",
        )

        products, errors = parse_product_csv(
            uploaded_file,
            self.organization,
            language="ru",
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["name"], "Кофе")
        self.assertEqual(products[0]["category_id"], self.category.pk)

    def test_super_inventory_advisor_recommends_stock_from_recent_sales(self):
        self.organization.plan = "super"
        self.organization.subscription_status = "active"
        self.organization.save(update_fields=["plan", "subscription_status"])
        fast_mover = Product.objects.create(
            organization=self.organization,
            name="Fast mover",
            sku="FAST-1",
            quantity=3,
            minimum_stock=2,
            unit_price=Decimal("1.00"),
        )
        Sale.objects.create(
            organization=self.organization,
            product=fast_mover,
            quantity=30,
            unit_price=Decimal("1.00"),
            total_price=Decimal("30.00"),
            created_by=self.user,
        )

        response = self.client.get(reverse("inventory_advisor"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_access"])
        suggestion = next(
            item
            for item in response.context["suggestions"]
            if item["product"] == fast_mover
        )
        self.assertEqual(suggestion["recommended_stock"], 14)
        self.assertEqual(suggestion["reorder_quantity"], 11)
        self.assertContains(response, "no external AI or API is used")
