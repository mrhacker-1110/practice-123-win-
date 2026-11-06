import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("warehouse", "0007_backfill_default_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="profiles",
                to="warehouse.organization",
            ),
        ),
        migrations.AlterField(
            model_name="category",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="categories",
                to="warehouse.organization",
            ),
        ),
        migrations.AlterField(
            model_name="supplier",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="suppliers",
                to="warehouse.organization",
            ),
        ),
        migrations.AlterField(
            model_name="customer",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="customers",
                to="warehouse.organization",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="products",
                to="warehouse.organization",
            ),
        ),
        migrations.AlterField(
            model_name="stockmovement",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="stock_movements",
                to="warehouse.organization",
            ),
        ),
        migrations.AlterField(
            model_name="sale",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sales",
                to="warehouse.organization",
            ),
        ),
        migrations.AlterField(
            model_name="purchase",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="purchases",
                to="warehouse.organization",
            ),
        ),
    ]
