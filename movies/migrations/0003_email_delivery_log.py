import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("movies", "0002_production_upgrade"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailDeliveryLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recipient", models.EmailField(max_length=254)),
                (
                    "status",
                    models.CharField(
                        choices=[("PENDING", "Pending"), ("SENT", "Sent"), ("FAILED", "Failed")],
                        db_index=True,
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "booking",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_logs",
                        to="movies.booking",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="emaildeliverylog",
            index=models.Index(fields=["status", "created_at"], name="email_status_time_idx"),
        ),
    ]
