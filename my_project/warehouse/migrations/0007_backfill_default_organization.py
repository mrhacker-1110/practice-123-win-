from django.conf import settings
from django.db import migrations


def assign_existing_data_to_workspace(apps, schema_editor):
    Organization = apps.get_model("warehouse", "Organization")
    UserProfile = apps.get_model("warehouse", "UserProfile")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    database = schema_editor.connection.alias

    users = User.objects.using(database).order_by("pk")
    owner = users.filter(is_superuser=True).first() or users.first()
    organization = Organization.objects.using(database).create(
        name="Main Warehouse",
        owner_id=owner.pk if owner else None,
    )

    for model_name in (
        "Category",
        "Supplier",
        "Customer",
        "Product",
        "StockMovement",
        "Sale",
        "Purchase",
    ):
        model = apps.get_model("warehouse", model_name)
        model.objects.using(database).filter(organization__isnull=True).update(
            organization_id=organization.pk
        )

    for user in users.iterator():
        UserProfile.objects.using(database).get_or_create(
            user_id=user.pk,
            defaults={
                "organization_id": organization.pk,
                "full_name": " ".join(
                    part for part in (user.first_name, user.last_name) if part
                ),
                "email_verified": user.is_active,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("warehouse", "0006_userprofile_alter_category_name_alter_product_sku_and_more"),
    ]

    operations = [
        migrations.RunPython(
            assign_existing_data_to_workspace,
            migrations.RunPython.noop,
        ),
    ]
