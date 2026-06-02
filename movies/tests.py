import hmac
from hashlib import sha256
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .constants import EmailStatus, PaymentStatus, ReservationStatus
from .models import Booking, EmailDeliveryLog, Genre, Language, Movie, PaymentTransaction, Seat, Theater
from .services import AnalyticsService, MovieFilterService, PaymentService, SeatReservationService
from .utils import build_youtube_embed_url, build_youtube_thumbnail_url
from .validators import validate_youtube_url


class ProductionFeatureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="tester",
            email="tester@example.com",
            password="pass12345",
        )
        self.movie = Movie.objects.create(
            name="Avengers",
            image="movies/test.jpg",
            rating=Decimal("8.9"),
            cast="RDJ, Chris Evans",
            description="Test movie",
            trailer_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        self.theater = Theater.objects.create(
            name="PVR",
            movie=self.movie,
            time=timezone.now() + timezone.timedelta(days=1),
        )
        self.seats = [
            Seat.objects.create(theater=self.theater, seat_number=f"A{number}")
            for number in range(1, 5)
        ]

    def test_secure_youtube_trailer_validation_and_embed(self):
        validate_youtube_url("https://youtu.be/dQw4w9WgXcQ")

        with self.assertRaises(ValidationError):
            validate_youtube_url("https://evil.example.com/watch?v=dQw4w9WgXcQ")

        embed_url = build_youtube_embed_url(self.movie.trailer_url)
        thumbnail_url = build_youtube_thumbnail_url(self.movie.trailer_url)
        self.assertTrue(embed_url.startswith("https://www.youtube-nocookie.com/embed/"))
        self.assertIn("modestbranding=1", embed_url)
        self.assertEqual(thumbnail_url, "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg")

    def test_seat_reservation_locks_and_expires_seats(self):
        reservation = SeatReservationService.reserve_seats(
            self.user,
            self.theater,
            [self.seats[0].id, self.seats[1].id],
        )

        self.assertEqual(reservation.status, ReservationStatus.LOCKED)
        with self.assertRaises(ValidationError):
            SeatReservationService.reserve_seats(self.user, self.theater, [self.seats[0].id])

        reservation.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        reservation.save(update_fields=["expires_at"])
        expired_count = SeatReservationService.expire_stale_reservations()
        reservation.refresh_from_db()

        self.assertEqual(expired_count, 1)
        self.assertEqual(reservation.status, ReservationStatus.EXPIRED)

    @override_settings(
        RAZORPAY_KEY_SECRET="test_secret",
        CELERY_TASK_ALWAYS_EAGER=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_payment_confirmation_is_signature_verified_and_idempotent(self):
        reservation = SeatReservationService.reserve_seats(
            self.user,
            self.theater,
            [self.seats[0].id, self.seats[1].id],
        )
        payment = PaymentTransaction.objects.create(
            reservation=reservation,
            user=self.user,
            amount=Decimal("500.00"),
            currency="INR",
            provider_order_id="order_test_123",
            idempotency_key=f"reservation:{reservation.id}",
        )
        provider_payment_id = "pay_test_123"
        signature = hmac.new(
            b"test_secret",
            f"{payment.provider_order_id}|{provider_payment_id}".encode(),
            sha256,
        ).hexdigest()

        bookings = PaymentService.confirm_payment(
            payment.provider_order_id,
            provider_payment_id,
            signature,
            user=self.user,
        )
        second_call = PaymentService.confirm_payment(
            payment.provider_order_id,
            provider_payment_id,
            signature,
            user=self.user,
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.CAPTURED)
        self.assertEqual(len(bookings), 2)
        self.assertEqual(len(second_call), 2)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertTrue(Seat.objects.get(id=self.seats[0].id).is_booked)

    def test_payment_cancellation_and_timeout_release_reservation(self):
        reservation = SeatReservationService.reserve_seats(
            self.user,
            self.theater,
            [self.seats[0].id],
        )
        payment = PaymentTransaction.objects.create(
            reservation=reservation,
            user=self.user,
            amount=Decimal("250.00"),
            currency="INR",
            provider_order_id="order_cancel_123",
            idempotency_key=f"reservation:{reservation.id}",
        )

        PaymentService.cancel_payment(reservation, user=self.user)
        reservation.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
        self.assertEqual(payment.status, PaymentStatus.CANCELLED)

        timeout_reservation = SeatReservationService.reserve_seats(
            self.user,
            self.theater,
            [self.seats[1].id],
        )
        timeout_payment = PaymentTransaction.objects.create(
            reservation=timeout_reservation,
            user=self.user,
            amount=Decimal("250.00"),
            currency="INR",
            provider_order_id="order_timeout_123",
            idempotency_key=f"reservation:{timeout_reservation.id}",
        )
        timeout_reservation.status = ReservationStatus.EXPIRED
        timeout_reservation.save(update_fields=["status"])
        self.assertEqual(PaymentService.expire_stale_payments(), 1)
        timeout_payment.refresh_from_db()
        self.assertEqual(timeout_payment.status, PaymentStatus.CANCELLED)

    def test_admin_analytics_uses_aggregated_data(self):
        booking = Booking.objects.create(
            user=self.user,
            seat=self.seats[0],
            movie=self.movie,
            theater=self.theater,
            amount=Decimal("250.00"),
            payment_id="pay_analytics",
        )
        self.seats[0].is_booked = True
        self.seats[0].save(update_fields=["is_booked"])

        data = AnalyticsService.dashboard_data()

        self.assertEqual(data["popular_movies"][0]["movie__name"], self.movie.name)
        self.assertEqual(data["popular_movies"][0]["bookings"], 1)
        self.assertEqual(data["busiest_theaters"][0]["name"], self.theater.name)
        self.assertEqual(data["busiest_theaters"][0]["booked_seats"], 1)
        self.assertIn("cancellation_rate", data)
        self.assertEqual(data["cancellation_rate"]["total"], 1)
        self.assertEqual(booking.amount, Decimal("250.00"))

    def test_genre_language_filtering_counts_and_sorting(self):
        action = Genre.objects.create(name="Action", slug="action")
        drama = Genre.objects.create(name="Drama", slug="drama")
        english = Language.objects.create(name="English", code="en")
        hindi = Language.objects.create(name="Hindi", code="hi")

        self.movie.genres.add(action)
        self.movie.languages.add(english)
        other = Movie.objects.create(
            name="Drama Film",
            image="movies/other.jpg",
            rating=Decimal("7.0"),
            cast="Cast",
            description="Other",
        )
        other.genres.add(drama)
        other.languages.add(hindi)

        request = RequestFactory().get("/movies/", {"genres": [str(action.id)], "languages": [str(english.id)]})
        movies = list(MovieFilterService.filtered_movies(request.GET))
        genres, languages = MovieFilterService.filter_counts(request.GET)

        self.assertEqual(movies, [self.movie])
        self.assertGreaterEqual(genres.get(id=action.id).movie_count, 1)
        self.assertGreaterEqual(languages.get(id=english.id).movie_count, 1)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_booking_confirmation_email_task_sends_email(self):
        from django.core import mail
        from .tasks import send_booking_confirmation_email

        booking = Booking.objects.create(
            user=self.user,
            seat=self.seats[0],
            movie=self.movie,
            theater=self.theater,
            amount=Decimal("250.00"),
            payment_id="pay_email",
        )

        send_booking_confirmation_email.delay([booking.id])

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.movie.name, mail.outbox[0].subject)
        self.assertIn("pay_email", mail.outbox[0].body)
        self.assertEqual(EmailDeliveryLog.objects.get(booking=booking).status, EmailStatus.SENT)
