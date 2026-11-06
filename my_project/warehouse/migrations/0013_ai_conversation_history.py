import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("warehouse", "0012_alter_organization_owner_alter_product_category_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AIConversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_conversations", to="warehouse.organization")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="warehouse_ai_conversations", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-updated_at"],
                "constraints": [models.UniqueConstraint(fields=("user", "organization"), name="ai_conversation_per_user_org")],
            },
        ),
        migrations.CreateModel(
            name="AIMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("user", "User"), ("model", "Assistant")], max_length=8)),
                ("content", models.TextField()),
                ("attachment_name", models.CharField(blank=True, max_length=255)),
                ("attachment_mime_type", models.CharField(blank=True, max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="warehouse.aiconversation")),
            ],
            options={
                "ordering": ["created_at", "pk"],
            },
        ),
        migrations.AddIndex(
            model_name="aimessage",
            index=models.Index(fields=["conversation", "created_at"], name="ai_msg_convo_time_idx"),
        ),
    ]
