import uuid

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .constants import (
    BookingStatus,
    PaymentProvider,
    PaymentStatus,
    ReservationStatus,
    EmailStatus,
)
from .validators import validate_youtube_url


class Movie(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    image = models.ImageField(upload_to="movies/")
    rating = models.DecimalField(max_digits=3,decimal_places=1)
    cast = models.TextField()
    description = models.TextField(blank=True,null=True) # optional
    trailer_url = models.URLField(
        blank=True,
        null=True,
        validators=[validate_youtube_url],
        help_text="Only youtube.com, youtu.be, youtube-nocookie.com, or m.youtube.com links are accepted.",
    )
    genres = models.ManyToManyField("Genre", related_name="movies", blank=True)
    languages = models.ManyToManyField("Language", related_name="movies", blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def trailer_embed_url(self):
        from .utils import build_youtube_embed_url

        return build_youtube_embed_url(self.trailer_url)

    @property
    def trailer_thumbnail_url(self):
        from .utils import build_youtube_thumbnail_url

        return build_youtube_thumbnail_url(self.trailer_url)

    @property
    def poster_url(self):
        from django.templatetags.static import static

        image_path = str(self.image or "")
        if image_path.startswith("movies/demo_posters/"):
            return static(image_path)
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        return static("movies/demo_posters/fallback.svg")


class Genre(models.Model):
    name = models.CharField(max_length=64, unique=True, db_index=True)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Language(models.Model):
    name = models.CharField(max_length=64, unique=True, db_index=True)
    code = models.CharField(max_length=16, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

class Theater(models.Model):
    name = models.CharField(max_length=255)
    movie = models.ForeignKey(Movie,on_delete=models.CASCADE,related_name='theaters')
    time = models.DateTimeField(db_index=True)

    def __str__(self):
        return f'{self.name} - {self.movie.name} at {self.time}'

    class Meta:
        indexes = [
            models.Index(fields=["movie", "time"], name="theater_movie_time_idx"),
            models.Index(fields=["name"], name="theater_name_idx"),
        ]

class Seat(models.Model):
    theater = models.ForeignKey(Theater,on_delete=models.CASCADE,related_name='seats')
    seat_number = models.CharField(max_length=10)
    is_booked=models.BooleanField(default=False)

    def __str__(self):
        return f'{self.seat_number} in {self.theater.name}'

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["theater", "seat_number"], name="unique_seat_per_theater"),
        ]
        indexes = [
            models.Index(fields=["theater", "is_booked"], name="seat_theater_booked_idx"),
        ]


class Reservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="seat_reservations")
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name="reservations")
    seats = models.ManyToManyField(Seat, through="ReservationSeat", related_name="reservations")
    status = models.CharField(
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.LOCKED,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    def __str__(self):
        return f"{self.user.username} reservation {self.id} ({self.status})"

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"], name="reservation_user_status_idx"),
            models.Index(fields=["status", "expires_at"], name="reservation_expiry_idx"),
        ]


class ReservationSeat(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE)
    seat = models.ForeignKey(Seat, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["reservation", "seat"], name="unique_seat_in_reservation"),
        ]
        indexes = [
            models.Index(fields=["seat"], name="reservation_seat_idx"),
        ]


class Booking(models.Model):
    user=models.ForeignKey(User,on_delete=models.CASCADE)
    seat=models.OneToOneField(Seat,on_delete=models.CASCADE)
    movie=models.ForeignKey(Movie,on_delete=models.CASCADE)
    theater=models.ForeignKey(Theater,on_delete=models.CASCADE)
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.SET_NULL,
        related_name="bookings",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.CONFIRMED,
        db_index=True,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    payment_id = models.CharField(max_length=128, blank=True, db_index=True)
    booked_at=models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Booking by{self.user.username} for {self.seat.seat_number} at {self.theater.name}'

    class Meta:
        indexes = [
            models.Index(fields=["user", "-booked_at"], name="booking_user_time_idx"),
            models.Index(fields=["movie", "status"], name="booking_movie_status_idx"),
            models.Index(fields=["theater", "status"], name="booking_theater_status_idx"),
            models.Index(fields=["booked_at"], name="booking_booked_at_idx"),
        ]


class PaymentTransaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(
        max_length=20,
        choices=PaymentProvider.choices,
        default=PaymentProvider.RAZORPAY,
    )
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name="payment")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="payments")
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.CREATED,
        db_index=True,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="INR")
    provider_order_id = models.CharField(max_length=128, unique=True, blank=True, null=True)
    provider_payment_id = models.CharField(max_length=128, unique=True, blank=True, null=True)
    provider_signature = models.CharField(max_length=256, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    failure_reason = models.TextField(blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.provider} payment {self.id} ({self.status})"

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"], name="payment_status_time_idx"),
        ]


class PaymentWebhookEvent(models.Model):
    provider = models.CharField(
        max_length=20,
        choices=PaymentProvider.choices,
        default=PaymentProvider.RAZORPAY,
    )
    event_id = models.CharField(max_length=160, unique=True)
    event_type = models.CharField(max_length=120, db_index=True)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider", "event_type"], name="webhook_provider_type_idx"),
            models.Index(fields=["received_at"], name="webhook_received_idx"),
        ]


class EmailDeliveryLog(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="email_logs")
    recipient = models.EmailField()
    status = models.CharField(
        max_length=20,
        choices=EmailStatus.choices,
        default=EmailStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"], name="email_status_time_idx"),
        ]
