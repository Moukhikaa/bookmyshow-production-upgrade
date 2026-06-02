import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .constants import EmailStatus
from .models import Booking, EmailDeliveryLog
from .services import PaymentService, SeatReservationService

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:
    class _InlineTask:
        def __init__(self, func, bind=False):
            self.func = func
            self.bind = bind

        def delay(self, *args, **kwargs):
            if self.bind:
                return self.func(self, *args, **kwargs)
            return self.func(*args, **kwargs)

        def __call__(self, *args, **kwargs):
            return self.delay(*args, **kwargs)

    def shared_task(*decorator_args, **decorator_kwargs):
        def decorate(func):
            return _InlineTask(func, bind=decorator_kwargs.get("bind", False))

        return decorate


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def send_booking_confirmation_email(self, booking_ids):
    bookings = list(
        Booking.objects.filter(id__in=booking_ids)
        .select_related("user", "movie", "theater", "seat")
        .order_by("seat__seat_number")
    )
    if not bookings:
        logger.warning("Booking confirmation email skipped; no bookings found for %s", booking_ids)
        return

    user = bookings[0].user
    if not user.email:
        logger.info("Booking confirmation email skipped; user %s has no email", user.id)
        return

    logs = [
        EmailDeliveryLog.objects.create(
            booking=booking,
            recipient=user.email,
            status=EmailStatus.PENDING,
            attempts=1,
        )
        for booking in bookings
    ]
    context = {"bookings": bookings, "user": user, "payment_id": bookings[0].payment_id}
    subject = f"Booking confirmed: {bookings[0].movie.name}"
    text_body = render_to_string("emails/booking_confirmation.txt", context)
    html_body = render_to_string("emails/booking_confirmation.html", context)
    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [user.email])
    message.attach_alternative(html_body, "text/html")
    try:
        message.send()
    except Exception as exc:
        logger.exception("Booking confirmation email failed for bookings %s", booking_ids)
        EmailDeliveryLog.objects.filter(id__in=[log.id for log in logs]).update(
            status=EmailStatus.FAILED,
            last_error=str(exc),
        )
        raise
    EmailDeliveryLog.objects.filter(id__in=[log.id for log in logs]).update(status=EmailStatus.SENT)


@shared_task
def expire_stale_reservations():
    return SeatReservationService.expire_stale_reservations()


@shared_task
def expire_stale_payments():
    return PaymentService.expire_stale_payments()
