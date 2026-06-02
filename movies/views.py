from datetime import date, timedelta

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect ,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .constants import ReservationStatus
from .models import Movie,Theater,Seat,Booking, Reservation, PaymentTransaction
from .services import AnalyticsService, MovieFilterService, PaymentService, SeatReservationService


def natural_seat_key(seat):
    prefix = "".join(char for char in seat.seat_number if not char.isdigit())
    number = "".join(char for char in seat.seat_number if char.isdigit())
    return (prefix.upper(), int(number or 0), seat.seat_number)


def current_booking_week():
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    return [week_start + timedelta(days=offset) for offset in range(7)]


def weekly_show_context(request, movie):
    week_dates = current_booking_week()
    now = timezone.now()
    week_shows = Theater.objects.filter(
        movie=movie,
        time__date__range=(week_dates[0], week_dates[-1]),
        time__gte=now,
    )
    counts = {
        row["show_date"]: row["show_count"]
        for row in week_shows.annotate(show_date=TruncDate("time"))
        .values("show_date")
        .annotate(show_count=Count("id"))
    }

    requested_date = None
    requested_value = request.GET.get("date")
    if requested_value:
        try:
            requested_date = date.fromisoformat(requested_value)
        except ValueError:
            requested_date = None

    if requested_date in week_dates:
        selected_date = requested_date
    elif counts.get(timezone.localdate()):
        selected_date = timezone.localdate()
    else:
        selected_date = next((day for day in week_dates if counts.get(day)), timezone.localdate())

    theaters = week_shows.filter(time__date=selected_date).order_by("time")
    week_schedule = [
        {
            "date": day,
            "show_count": counts.get(day, 0),
            "is_selected": day == selected_date,
            "is_weekend": day.weekday() >= 5,
        }
        for day in week_dates
    ]
    return {
        "theaters": theaters,
        "week_schedule": week_schedule,
        "selected_date": selected_date,
        "week_start": week_dates[0],
        "week_end": week_dates[-1],
    }

def movie_list(request):
    movies = MovieFilterService.filtered_movies(request.GET)
    paginator = Paginator(movies, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    genres, languages = MovieFilterService.filter_counts(request.GET)
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        'movies/movie_list.html',
        {
            'movies': page_obj.object_list,
            "page_obj": page_obj,
            "genres": genres,
            "languages": languages,
            "selected_genres": request.GET.getlist("genres"),
            "selected_languages": request.GET.getlist("languages"),
            "querystring": query_params.urlencode(),
        },
    )


def movie_detail(request, movie_id):
    movie = get_object_or_404(
        Movie.objects.prefetch_related("genres", "languages"),
        id=movie_id,
        is_active=True,
    )
    context = {"movie": movie}
    context.update(weekly_show_context(request, movie))
    return render(request, "movies/movie_detail.html", context)

def theater_list(request,movie_id):
    movie = get_object_or_404(Movie.objects.prefetch_related("genres", "languages"), id=movie_id)
    context = {"movie": movie}
    context.update(weekly_show_context(request, movie))
    return render(request,'movies/theater_list.html', context)



@login_required(login_url='/login/')
def book_seats(request,theater_id):
    theaters=get_object_or_404(Theater,id=theater_id)
    SeatReservationService.expire_stale_reservations()
    locked_seat_ids = list(Reservation.objects.filter(
        theater=theaters,
        status=ReservationStatus.LOCKED,
        expires_at__gt=timezone.now(),
    ).values_list("seats__id", flat=True))
    seats=list(Seat.objects.filter(theater=theaters))
    seats.sort(key=natural_seat_key)
    if request.method=='POST':
        selected_Seats= request.POST.getlist('seats')
        if not selected_Seats:
            return render(request,"movies/seat_selection.html",{'theaters':theaters,"seats":seats, "locked_seat_ids": locked_seat_ids, 'error':"No seat selected"})
        try:
            reservation = SeatReservationService.reserve_seats(
                request.user,
                theaters,
                selected_Seats,
            )
            payment = PaymentService.create_payment_order(reservation)
        except ValidationError as exc:
            return render(
                request,
                'movies/seat_selection.html',
                {'theaters':theaters,"seats":seats, "locked_seat_ids": locked_seat_ids, 'error': exc.messages[0]},
            )
        return redirect("payment_checkout", reservation_id=reservation.id)
    return render(request,'movies/seat_selection.html',{'theaters':theaters,"seats":seats, "locked_seat_ids": locked_seat_ids})


@login_required(login_url="/login/")
def payment_checkout(request, reservation_id):
    reservation = get_object_or_404(
        Reservation.objects.select_related("theater", "theater__movie").prefetch_related("seats"),
        id=reservation_id,
        user=request.user,
    )
    if reservation.status != ReservationStatus.LOCKED or reservation.is_expired:
        SeatReservationService.mark_expired(reservation)
        return render(request, "movies/payment_checkout.html", {"reservation": reservation, "expired": True})

    try:
        payment = PaymentService.create_payment_order(reservation)
    except ValidationError as exc:
        return render(request, "movies/payment_failed.html", {"error": exc.messages[0]})
    return render(
        request,
        "movies/payment_checkout.html",
        {
            "reservation": reservation,
            "payment": payment,
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "allow_development_payments": settings.ALLOW_DEVELOPMENT_PAYMENTS,
            "amount_paise": int(payment.amount * 100),
            "callback_url": request.build_absolute_uri(reverse("payment_success")),
        },
    )


@login_required(login_url="/login/")
@require_POST
def payment_success(request):
    try:
        bookings = PaymentService.confirm_payment(
            provider_order_id=request.POST.get("razorpay_order_id", ""),
            provider_payment_id=request.POST.get("razorpay_payment_id", ""),
            signature=request.POST.get("razorpay_signature", ""),
            user=request.user,
        )
    except (ValidationError, PaymentTransaction.DoesNotExist) as exc:
        return render(request, "movies/payment_failed.html", {"error": str(exc)})
    return render(request, "movies/payment_success.html", {"bookings": bookings})


@login_required(login_url="/login/")
@require_POST
def payment_cancel(request, reservation_id):
    reservation = get_object_or_404(Reservation, id=reservation_id, user=request.user)
    PaymentService.cancel_payment(reservation, user=request.user)
    return render(request, "movies/payment_failed.html", {"error": "Payment was cancelled. Your seats were released."})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    try:
        PaymentService.record_webhook(
            request.body,
            request.headers.get("X-Razorpay-Signature", ""),
        )
    except ValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    return HttpResponse("ok")


@staff_member_required
def analytics_dashboard(request):
    return render(request, "movies/admin_analytics.html", AnalyticsService.dashboard_data())
