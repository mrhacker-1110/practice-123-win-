from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("warehouse", "0013_ai_conversation_history"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="aiconversation",
            name="ai_conversation_per_user_org",
        ),
        migrations.AddField(
            model_name="aiconversation",
            name="title",
            field=models.CharField(default="Новый чат", max_length=120),
        ),
    ]
