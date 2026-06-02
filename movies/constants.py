from django.db import models


class ReservationStatus(models.TextChoices):
    LOCKED = "LOCKED", "Locked"
    BOOKED = "BOOKED", "Booked"
    EXPIRED = "EXPIRED", "Expired"
    CANCELLED = "CANCELLED", "Cancelled"


class BookingStatus(models.TextChoices):
    CONFIRMED = "CONFIRMED", "Confirmed"
    CANCELLED = "CANCELLED", "Cancelled"


class PaymentStatus(models.TextChoices):
    CREATED = "CREATED", "Created"
    AUTHORIZED = "AUTHORIZED", "Authorized"
    CAPTURED = "CAPTURED", "Captured"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class PaymentProvider(models.TextChoices):
    RAZORPAY = "RAZORPAY", "Razorpay"


class EmailStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"


DEFAULT_SEAT_LOCK_SECONDS = 120
