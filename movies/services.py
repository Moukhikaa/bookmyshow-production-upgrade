import hmac
import json
import logging
from decimal import Decimal
from hashlib import sha256

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, ExpressionWrapper, F, FloatField, Q, Sum
from django.utils import timezone

from .constants import (
    DEFAULT_SEAT_LOCK_SECONDS,
    BookingStatus,
    PaymentProvider,
    PaymentStatus,
    ReservationStatus,
)
from .models import (
    Booking,
    Genre,
    Language,
    PaymentTransaction,
    PaymentWebhookEvent,
    Reservation,
    ReservationSeat,
    Movie,
    Seat,
    Theater,
)

logger = logging.getLogger(__name__)


class SeatReservationService:
    @staticmethod
    def expire_stale_reservations():
        now = timezone.now()
        with transaction.atomic():
            return Reservation.objects.select_for_update().filter(
                status=ReservationStatus.LOCKED,
                expires_at__lte=now,
            ).update(status=ReservationStatus.EXPIRED)

    @staticmethod
    @transaction.atomic
    def reserve_seats(user, theater, seat_ids, idempotency_key=None):
        if not seat_ids:
            raise ValidationError("No seats selected.")

        SeatReservationService.expire_stale_reservations()
        normalized_ids = sorted({int(seat_id) for seat_id in seat_ids})
        locked_seats = list(
            Seat.objects.select_for_update()
            .filter(id__in=normalized_ids, theater=theater)
            .order_by("id")
        )

        if len(locked_seats) != len(normalized_ids):
            raise ValidationError("One or more selected seats are invalid for this theater.")

        already_booked = [seat.seat_number for seat in locked_seats if seat.is_booked]
        if already_booked:
            raise ValidationError(f"Already booked: {', '.join(already_booked)}")

        active_locks = (
            ReservationSeat.objects.select_for_update()
            .filter(
                seat_id__in=normalized_ids,
                reservation__status=ReservationStatus.LOCKED,
                reservation__expires_at__gt=timezone.now(),
            )
            .select_related("seat")
        )
        locked_numbers = [lock.seat.seat_number for lock in active_locks]
        if locked_numbers:
            raise ValidationError(f"Temporarily locked: {', '.join(locked_numbers)}")

        key = idempotency_key or f"{user.id}:{theater.id}:{','.join(map(str, normalized_ids))}:{timezone.now().timestamp()}"
        reservation, created = Reservation.objects.get_or_create(
            idempotency_key=key,
            defaults={
                "user": user,
                "theater": theater,
                "status": ReservationStatus.LOCKED,
                "expires_at": timezone.now() + timezone.timedelta(seconds=DEFAULT_SEAT_LOCK_SECONDS),
            },
        )
        if not created:
            return reservation

        ReservationSeat.objects.bulk_create(
            [ReservationSeat(reservation=reservation, seat=seat) for seat in locked_seats]
        )
        return reservation

    @staticmethod
    @transaction.atomic
    def cancel_reservation(reservation, reason="Reservation cancelled."):
        reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
        if reservation.status == ReservationStatus.LOCKED:
            reservation.status = ReservationStatus.CANCELLED
            reservation.save(update_fields=["status", "updated_at"])
        payment = getattr(reservation, "payment", None)
        if payment and payment.status in {PaymentStatus.CREATED, PaymentStatus.AUTHORIZED}:
            payment.status = PaymentStatus.CANCELLED
            payment.failure_reason = reason
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
        return reservation

    @staticmethod
    @transaction.atomic
    def mark_expired(reservation):
        reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
        if reservation.status == ReservationStatus.LOCKED and reservation.is_expired:
            reservation.status = ReservationStatus.EXPIRED
            reservation.save(update_fields=["status", "updated_at"])
        return reservation


class PaymentService:
    @staticmethod
    def calculate_amount(reservation):
        seat_count = reservation.seats.count()
        price = Decimal(str(getattr(settings, "DEFAULT_TICKET_PRICE", "250.00")))
        return price * seat_count

    @staticmethod
    @transaction.atomic
    def create_payment_order(reservation):
        reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
        if reservation.status != ReservationStatus.LOCKED or reservation.is_expired:
            raise ValidationError("Reservation expired. Please select seats again.")

        amount = PaymentService.calculate_amount(reservation)
        payment, created = PaymentTransaction.objects.get_or_create(
            reservation=reservation,
            defaults={
                "user": reservation.user,
                "amount": amount,
                "currency": "INR",
                "idempotency_key": f"reservation:{reservation.id}",
            },
        )
        if payment.provider_order_id:
            return payment

        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            if not settings.ALLOW_DEVELOPMENT_PAYMENTS:
                raise ValidationError("Payment gateway is not configured.")
            payment.provider_order_id = f"dev_order_{reservation.id}"
            payment.raw_response = {"mode": "development"}
            payment.save(update_fields=["provider_order_id", "raw_response", "updated_at"])
            return payment

        import razorpay

        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        response = client.order.create(
            {
                "amount": int(amount * 100),
                "currency": "INR",
                "receipt": str(reservation.id),
                "payment_capture": 1,
                "notes": {"reservation_id": str(reservation.id), "user_id": reservation.user_id},
            }
        )
        payment.provider_order_id = response["id"]
        payment.raw_response = response
        payment.save(update_fields=["provider_order_id", "raw_response", "updated_at"])
        return payment

    @staticmethod
    @transaction.atomic
    def cancel_payment(reservation, user=None, reason="User cancelled payment."):
        reservations = Reservation.objects.select_for_update()
        if user is not None:
            reservations = reservations.filter(user=user)
        reservation = reservations.get(pk=reservation.pk)
        return SeatReservationService.cancel_reservation(reservation, reason=reason)

    @staticmethod
    @transaction.atomic
    def expire_stale_payments():
        now = timezone.now()
        stale_payments = PaymentTransaction.objects.select_for_update().filter(
            status__in=[PaymentStatus.CREATED, PaymentStatus.AUTHORIZED],
            reservation__status=ReservationStatus.EXPIRED,
        )
        count = stale_payments.update(
            status=PaymentStatus.CANCELLED,
            failure_reason="Payment timed out because the seat reservation expired.",
            updated_at=now,
        )
        return count

    @staticmethod
    def verify_checkout_signature(order_id, payment_id, signature):
        if not settings.RAZORPAY_KEY_SECRET:
            return (
                settings.ALLOW_DEVELOPMENT_PAYMENTS
                and order_id.startswith("dev_order_")
                and payment_id.startswith("dev_payment_")
            )

        message = f"{order_id}|{payment_id}".encode()
        digest = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), message, sha256).hexdigest()
        return hmac.compare_digest(digest, signature or "")

    @staticmethod
    @transaction.atomic
    def confirm_payment(provider_order_id, provider_payment_id, signature, user=None):
        payments = PaymentTransaction.objects.select_for_update().select_related("reservation")
        if user is not None:
            payments = payments.filter(user=user)
        payment = payments.get(provider_order_id=provider_order_id)
        if payment.status == PaymentStatus.CAPTURED:
            return list(payment.reservation.bookings.all())

        if not PaymentService.verify_checkout_signature(provider_order_id, provider_payment_id, signature):
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = "Invalid Razorpay signature"
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
            raise ValidationError("Payment verification failed.")

        payment.provider_payment_id = provider_payment_id
        payment.provider_signature = signature or ""
        bookings = BookingService.confirm_reservation(payment.reservation, payment)
        payment.status = PaymentStatus.CAPTURED
        payment.save(
            update_fields=["provider_payment_id", "provider_signature", "status", "updated_at"]
        )
        return bookings

    @staticmethod
    def verify_webhook_signature(body, signature):
        if not settings.RAZORPAY_WEBHOOK_SECRET:
            return False
        digest = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, sha256).hexdigest()
        return hmac.compare_digest(digest, signature or "")

    @staticmethod
    @transaction.atomic
    def record_webhook(body, signature):
        if not PaymentService.verify_webhook_signature(body, signature):
            raise ValidationError("Invalid webhook signature.")

        payload = json.loads(body.decode("utf-8"))
        event_id = payload.get("event_id") or payload.get("id") or sha256(body).hexdigest()
        event_type = payload.get("event", "")
        if not event_id:
            raise ValidationError("Webhook event id missing.")

        event, created = PaymentWebhookEvent.objects.get_or_create(
            provider=PaymentProvider.RAZORPAY,
            event_id=event_id,
            defaults={"event_type": event_type, "payload": payload},
        )
        if not created:
            return event

        order_id = (
            payload.get("payload", {})
            .get("payment", {})
            .get("entity", {})
            .get("order_id")
        )
        payment_id = (
            payload.get("payload", {})
            .get("payment", {})
            .get("entity", {})
            .get("id")
        )
        if event_type == "payment.captured" and order_id and payment_id:
            payment = PaymentTransaction.objects.select_for_update().filter(provider_order_id=order_id).first()
            if payment and payment.status != PaymentStatus.CAPTURED:
                payment.provider_payment_id = payment_id
                BookingService.confirm_reservation(payment.reservation, payment)
                payment.status = PaymentStatus.CAPTURED
                payment.save(update_fields=["provider_payment_id", "status", "updated_at"])
        elif event_type == "payment.failed" and order_id:
            payment = PaymentTransaction.objects.select_for_update().filter(provider_order_id=order_id).first()
            if payment and payment.status != PaymentStatus.CAPTURED:
                payment.provider_payment_id = payment_id or ""
                payment.status = PaymentStatus.FAILED
                payment.failure_reason = "Payment failed webhook received from Razorpay."
                payment.save(
                    update_fields=["provider_payment_id", "status", "failure_reason", "updated_at"]
                )

        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at"])
        return event


class BookingService:
    @staticmethod
    @transaction.atomic
    def confirm_reservation(reservation, payment):
        reservation = Reservation.objects.select_for_update().prefetch_related("seats").get(pk=reservation.pk)
        if reservation.status == ReservationStatus.BOOKED:
            return list(reservation.bookings.all())
        if reservation.status != ReservationStatus.LOCKED or reservation.is_expired:
            raise ValidationError("Reservation is no longer active.")

        seats = list(Seat.objects.select_for_update().filter(id__in=reservation.seats.values("id")).order_by("id"))
        if any(seat.is_booked for seat in seats):
            raise IntegrityError("A selected seat was booked before confirmation completed.")

        bookings = []
        per_seat_amount = payment.amount / max(len(seats), 1)
        for seat in seats:
            bookings.append(
                Booking.objects.create(
                    user=reservation.user,
                    seat=seat,
                    movie=reservation.theater.movie,
                    theater=reservation.theater,
                    reservation=reservation,
                    status=BookingStatus.CONFIRMED,
                    amount=per_seat_amount,
                    payment_id=payment.provider_payment_id or payment.provider_order_id or "",
                )
            )
            seat.is_booked = True
        Seat.objects.bulk_update(seats, ["is_booked"])
        reservation.status = ReservationStatus.BOOKED
        reservation.save(update_fields=["status", "updated_at"])
        from .tasks import send_booking_confirmation_email

        transaction.on_commit(lambda: send_booking_confirmation_email.delay([booking.id for booking in bookings]))
        return bookings


class MovieFilterService:
    SORT_MAP = {
        "rating": "-rating",
        "newest": "-created_at",
        "name": "name",
    }

    @staticmethod
    def filtered_movies(params):
        movies = Movie.objects.filter(is_active=True).prefetch_related("genres", "languages")
        search = params.get("search")
        genre_ids = [value for value in params.getlist("genres") if value.isdigit()]
        language_ids = [value for value in params.getlist("languages") if value.isdigit()]
        sort = MovieFilterService.SORT_MAP.get(params.get("sort"), "name")

        if search:
            movies = movies.filter(name__icontains=search)
        if genre_ids:
            movies = movies.filter(genres__id__in=genre_ids)
        if language_ids:
            movies = movies.filter(languages__id__in=language_ids)
        return movies.distinct().order_by(sort)

    @staticmethod
    def filter_counts(base_params):
        selected_genres = [value for value in base_params.getlist("genres") if value.isdigit()]
        selected_languages = [value for value in base_params.getlist("languages") if value.isdigit()]

        base_movies = Movie.objects.filter(is_active=True)
        if selected_languages:
            genre_base = base_movies.filter(languages__id__in=selected_languages)
        else:
            genre_base = base_movies
        if selected_genres:
            language_base = base_movies.filter(genres__id__in=selected_genres)
        else:
            language_base = base_movies

        genres = Genre.objects.annotate(
            movie_count=Count("movies", filter=Q(movies__in=genre_base), distinct=True)
        )
        languages = Language.objects.annotate(
            movie_count=Count("movies", filter=Q(movies__in=language_base), distinct=True)
        )
        return genres, languages


class AnalyticsService:
    @staticmethod
    def dashboard_data():
        from django.core.cache import cache
        from django.db.models.functions import ExtractHour, TruncDate, TruncMonth, TruncWeek

        cache_key = "admin_analytics_dashboard:v1"
        cached = cache.get(cache_key)
        if cached:
            return cached

        confirmed = Booking.objects.filter(status=BookingStatus.CONFIRMED)
        data = {
            "daily_revenue": list(
                confirmed.annotate(period=TruncDate("booked_at"))
                .values("period")
                .annotate(total=Sum("amount"), bookings=Count("id"))
                .order_by("-period")[:30]
            ),
            "weekly_revenue": list(
                confirmed.annotate(period=TruncWeek("booked_at"))
                .values("period")
                .annotate(total=Sum("amount"), bookings=Count("id"))
                .order_by("-period")[:12]
            ),
            "monthly_revenue": list(
                confirmed.annotate(period=TruncMonth("booked_at"))
                .values("period")
                .annotate(total=Sum("amount"), bookings=Count("id"))
                .order_by("-period")[:12]
            ),
            "popular_movies": list(
                confirmed.values("movie__name")
                .annotate(bookings=Count("id"), revenue=Sum("amount"))
                .order_by("-bookings")[:10]
            ),
            "busiest_theaters": list(
                Theater.objects.annotate(
                    total_seats=Count("seats", distinct=True),
                    booked_seats=Count(
                        "booking",
                        filter=Q(booking__status=BookingStatus.CONFIRMED),
                        distinct=True,
                    ),
                    revenue=Sum(
                        "booking__amount",
                        filter=Q(booking__status=BookingStatus.CONFIRMED),
                    ),
                )
                .filter(total_seats__gt=0)
                .annotate(
                    occupancy_rate=ExpressionWrapper(
                        F("booked_seats") * 100.0 / F("total_seats"),
                        output_field=FloatField(),
                    )
                )
                .values("name", "booked_seats", "total_seats", "occupancy_rate", "revenue")
                .order_by("-occupancy_rate", "-booked_seats")[:10]
            ),
            "peak_hours": list(
                confirmed.annotate(hour=ExtractHour("booked_at"))
                .values("hour")
                .annotate(bookings=Count("id"))
                .order_by("-bookings")[:24]
            ),
            "cancellation_breakdown": list(Booking.objects.values("status").annotate(total=Count("id"))),
        }
        total_bookings = Booking.objects.count()
        cancelled_bookings = Booking.objects.filter(status=BookingStatus.CANCELLED).count()
        data["cancellation_rate"] = {
            "total": total_bookings,
            "cancelled": cancelled_bookings,
            "percent": (cancelled_bookings * 100.0 / total_bookings) if total_bookings else 0,
        }
        cache.set(cache_key, data, 300)
        return data
