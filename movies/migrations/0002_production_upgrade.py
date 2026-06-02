import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

import movies.validators


def populate_movie_created_at(apps, schema_editor):
    Movie = apps.get_model("movies", "Movie")
    Movie.objects.filter(created_at__isnull=True).update(created_at=timezone.now())


def normalize_duplicate_seats(apps, schema_editor):
    from django.db.models import Count

    Seat = apps.get_model("movies", "Seat")
    Booking = apps.get_model("movies", "Booking")
    db = schema_editor.connection.alias

    duplicate_groups = (
        Seat.objects.using(db)
        .values("theater_id", "seat_number")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )

    for group in duplicate_groups:
        seats = list(
            Seat.objects.using(db)
            .filter(theater_id=group["theater_id"], seat_number=group["seat_number"])
            .order_by("-is_booked", "id")
        )
        keep = seats[0]
        for index, duplicate in enumerate(seats[1:], start=1):
            has_booking = Booking.objects.using(db).filter(seat_id=duplicate.id).exists()
            if has_booking:
                duplicate.seat_number = _unique_legacy_seat_number(
                    Seat,
                    db,
                    duplicate.theater_id,
                    duplicate.id,
                    index,
                    keep.seat_number,
                )
                duplicate.save(update_fields=["seat_number"])
            else:
                duplicate.delete()


def _unique_legacy_seat_number(Seat, db, theater_id, seat_id, index, original):
    suffix_seed = f"D{seat_id}"[:10]
    candidate = suffix_seed
    counter = index
    while (
        Seat.objects.using(db)
        .filter(theater_id=theater_id, seat_number=candidate)
        .exclude(id=seat_id)
        .exists()
    ):
        suffix = str(counter)
        candidate = f"{original[: max(1, 10 - len(suffix))]}{suffix}"[:10]
        counter += 1
    return candidate


class Migration(migrations.Migration):

    dependencies = [
        ("movies", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Genre",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=64, unique=True)),
                ("slug", models.SlugField(max_length=80, unique=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Language",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=64, unique=True)),
                ("code", models.CharField(blank=True, max_length=16, null=True, unique=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="movie",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.RunPython(populate_movie_created_at, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="movie",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AddField(
            model_name="movie",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="movie",
            name="trailer_url",
            field=models.URLField(
                blank=True,
                help_text="Only youtube.com, youtu.be, youtube-nocookie.com, or m.youtube.com links are accepted.",
                null=True,
                validators=[movies.validators.validate_youtube_url],
            ),
        ),
        migrations.AddField(
            model_name="movie",
            name="genres",
            field=models.ManyToManyField(blank=True, related_name="movies", to="movies.Genre"),
        ),
        migrations.AddField(
            model_name="movie",
            name="languages",
            field=models.ManyToManyField(blank=True, related_name="movies", to="movies.Language"),
        ),
        migrations.AlterField(
            model_name="movie",
            name="name",
            field=models.CharField(db_index=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="theater",
            name="time",
            field=models.DateTimeField(db_index=True),
        ),
        migrations.CreateModel(
            name="Reservation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("LOCKED", "Locked"), ("BOOKED", "Booked"), ("EXPIRED", "Expired"), ("CANCELLED", "Cancelled")], db_index=True, default="LOCKED", max_length=20)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("idempotency_key", models.CharField(max_length=128, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("theater", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reservations", to="movies.theater")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="seat_reservations", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ReservationSeat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reservation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="movies.reservation")),
                ("seat", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="movies.seat")),
            ],
        ),
        migrations.AddField(
            model_name="reservation",
            name="seats",
            field=models.ManyToManyField(related_name="reservations", through="movies.ReservationSeat", to="movies.Seat"),
        ),
        migrations.AddField(
            model_name="booking",
            name="amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="booking",
            name="payment_id",
            field=models.CharField(blank=True, db_index=True, max_length=128),
        ),
        migrations.AddField(
            model_name="booking",
            name="reservation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bookings", to="movies.reservation"),
        ),
        migrations.AddField(
            model_name="booking",
            name="status",
            field=models.CharField(choices=[("CONFIRMED", "Confirmed"), ("CANCELLED", "Cancelled")], db_index=True, default="CONFIRMED", max_length=20),
        ),
        migrations.CreateModel(
            name="PaymentTransaction",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("provider", models.CharField(choices=[("RAZORPAY", "Razorpay")], default="RAZORPAY", max_length=20)),
                ("status", models.CharField(choices=[("CREATED", "Created"), ("AUTHORIZED", "Authorized"), ("CAPTURED", "Captured"), ("FAILED", "Failed"), ("CANCELLED", "Cancelled")], db_index=True, default="CREATED", max_length=20)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10, validators=[django.core.validators.MinValueValidator(0)])),
                ("currency", models.CharField(default="INR", max_length=3)),
                ("provider_order_id", models.CharField(blank=True, max_length=128, null=True, unique=True)),
                ("provider_payment_id", models.CharField(blank=True, max_length=128, null=True, unique=True)),
                ("provider_signature", models.CharField(blank=True, max_length=256)),
                ("idempotency_key", models.CharField(max_length=128, unique=True)),
                ("failure_reason", models.TextField(blank=True)),
                ("raw_response", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reservation", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="payment", to="movies.reservation")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PaymentWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("RAZORPAY", "Razorpay")], default="RAZORPAY", max_length=20)),
                ("event_id", models.CharField(max_length=160, unique=True)),
                ("event_type", models.CharField(db_index=True, max_length=120)),
                ("payload", models.JSONField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("processing_error", models.TextField(blank=True)),
            ],
        ),
        migrations.RunPython(normalize_duplicate_seats, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="seat",
            constraint=models.UniqueConstraint(fields=("theater", "seat_number"), name="unique_seat_per_theater"),
        ),
        migrations.AddConstraint(
            model_name="reservationseat",
            constraint=models.UniqueConstraint(fields=("reservation", "seat"), name="unique_seat_in_reservation"),
        ),
        migrations.AddIndex(
            model_name="theater",
            index=models.Index(fields=["movie", "time"], name="theater_movie_time_idx"),
        ),
        migrations.AddIndex(
            model_name="theater",
            index=models.Index(fields=["name"], name="theater_name_idx"),
        ),
        migrations.AddIndex(
            model_name="seat",
            index=models.Index(fields=["theater", "is_booked"], name="seat_theater_booked_idx"),
        ),
        migrations.AddIndex(
            model_name="reservation",
            index=models.Index(fields=["user", "status"], name="reservation_user_status_idx"),
        ),
        migrations.AddIndex(
            model_name="reservation",
            index=models.Index(fields=["status", "expires_at"], name="reservation_expiry_idx"),
        ),
        migrations.AddIndex(
            model_name="reservationseat",
            index=models.Index(fields=["seat"], name="reservation_seat_idx"),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(fields=["user", "-booked_at"], name="booking_user_time_idx"),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(fields=["movie", "status"], name="booking_movie_status_idx"),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(fields=["theater", "status"], name="booking_theater_status_idx"),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(fields=["booked_at"], name="booking_booked_at_idx"),
        ),
        migrations.AddIndex(
            model_name="paymenttransaction",
            index=models.Index(fields=["status", "created_at"], name="payment_status_time_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentwebhookevent",
            index=models.Index(fields=["provider", "event_type"], name="webhook_provider_type_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentwebhookevent",
            index=models.Index(fields=["received_at"], name="webhook_received_idx"),
        ),
    ]
