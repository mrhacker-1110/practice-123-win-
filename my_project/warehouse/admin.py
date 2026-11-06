from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

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

User = get_user_model()
admin.site.index_template = "admin/warehouse_index.html"


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    extra = 0
    can_delete = False
    fields = (
        "organization",
        "full_name",
        "phone",
        "email_verified",
        "terms_accepted_at",
        "terms_version",
        "created_at",
    )
    readonly_fields = fields


admin.site.unregister(User)


@admin.register(User)
class WarehouseUserAdmin(UserAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "email_verified",
        "is_staff",
        "is_active",
        "date_joined",
    )
    list_filter = (*UserAdmin.list_filter, "warehouse_profile__email_verified")
    search_fields = ("username", "email", "first_name", "last_name")
    inlines = (*UserAdmin.inlines, UserProfileInline)
    list_select_related = ("warehouse_profile",)

    @admin.display(boolean=True, description="Email verified")
    def email_verified(self, user):
        profile = getattr(user, "warehouse_profile", None)
        return profile.email_verified if profile else False

    def has_delete_permission(self, _request, _obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "plan",
        "subscription_status",
        "member_count",
        "created_at",
    )
    list_filter = ("plan", "subscription_status", "created_at")
    search_fields = ("name", "owner__username", "owner__email")
    list_select_related = ("owner",)

    @admin.display(description="Members")
    def member_count(self, organization):
        return organization.profiles.count()

    def has_delete_permission(self, _request, _obj=None):
        return False


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "organization",
        "full_name",
        "phone",
        "email_verified",
        "terms_accepted_at",
    )
    list_filter = ("email_verified", "terms_version", "created_at")
    search_fields = (
        "user__username",
        "user__email",
        "full_name",
        "phone",
        "organization__name",
    )
    list_select_related = ("user", "organization")
    readonly_fields = tuple(field.name for field in UserProfile._meta.fields)
    fields = readonly_fields

    def has_add_permission(self, _request):
        return False

    def has_delete_permission(self, _request, _obj=None):
        return False


class PreservedRecordAdmin(admin.ModelAdmin):
    def has_delete_permission(self, _request, _obj=None):
        return False


admin.site.register((Category, Supplier, Customer), PreservedRecordAdmin)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "category", "quantity", "minimum_stock", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "sku")
    list_select_related = ("category", "supplier")

    def save_model(self, request, obj, form, change):
        previous_quantity = (
            Product.objects.get(pk=obj.pk).quantity if change else None
        )
        super().save_model(request, obj, form, change)
        if previous_quantity is None and obj.quantity:
            StockMovement.objects.create(
                product=obj,
                movement_type="adjust",
                quantity=obj.quantity,
                notes="Initial stock entered in admin",
                created_by=request.user,
            )
        elif previous_quantity is not None and previous_quantity != obj.quantity:
            StockMovement.objects.create(
                product=obj,
                movement_type="adjust",
                quantity=obj.quantity,
                notes=f"Admin stock adjustment: {previous_quantity} -> {obj.quantity}",
                created_by=request.user,
            )

    def has_delete_permission(self, _request, _obj=None):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "movement_type", "quantity", "created_by", "created_at")
    list_filter = ("movement_type", "created_at")
    search_fields = ("product__name", "product__sku", "notes")
    list_select_related = ("product", "created_by")
    readonly_fields = tuple(field.name for field in StockMovement._meta.fields)

    def has_add_permission(self, _request):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False

    def has_delete_permission(self, _request, _obj=None):
        return False


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("product", "customer", "quantity", "total_price", "created_by", "sold_at")
    list_filter = ("sold_at",)
    search_fields = ("product__name", "product__sku", "customer__name")
    list_select_related = ("product", "customer", "created_by")
    readonly_fields = tuple(field.name for field in Sale._meta.fields)

    def has_add_permission(self, _request):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False

    def has_delete_permission(self, _request, _obj=None):
        return False


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("product", "supplier", "quantity", "total_cost", "created_by", "received_at")
    list_filter = ("received_at",)
    search_fields = ("product__name", "product__sku", "supplier__name", "reference")
    list_select_related = ("product", "supplier", "created_by")
    readonly_fields = tuple(field.name for field in Purchase._meta.fields)

    def has_add_permission(self, _request):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False

    def has_delete_permission(self, _request, _obj=None):
        return False
