from django.contrib import admin

from .models import (
    Booking,
    EmailDeliveryLog,
    Genre,
    Language,
    Movie,
    PaymentTransaction,
    PaymentWebhookEvent,
    Reservation,
    ReservationSeat,
    Seat,
    Theater,
)

@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['name', 'rating', 'is_active']
    list_filter = ["is_active", "genres", "languages"]
    search_fields = ["name", "cast"]
    filter_horizontal = ["genres", "languages"]


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name"]


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    search_fields = ["name", "code"]

@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'movie', 'time']
    list_filter = ["movie", "time"]
    search_fields = ["name", "movie__name"]

@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ['theater', 'seat_number', 'is_booked']
    list_filter = ["is_booked", "theater"]
    search_fields = ["seat_number", "theater__name"]


class ReservationSeatInline(admin.TabularInline):
    model = ReservationSeat
    extra = 0
    readonly_fields = ["created_at"]


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "theater", "status", "expires_at", "created_at"]
    list_filter = ["status", "created_at", "expires_at"]
    search_fields = ["id", "user__username", "idempotency_key"]
    readonly_fields = ["id", "created_at", "updated_at"]
    inlines = [ReservationSeatInline]

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['user', 'seat', 'movie','theater', 'status', 'amount', 'booked_at']
    list_filter = ["status", "movie", "theater", "booked_at"]
    search_fields = ["user__username", "movie__name", "theater__name", "payment_id"]
    date_hierarchy = "booked_at"


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ["id", "provider", "user", "status", "amount", "currency", "created_at"]
    list_filter = ["provider", "status", "created_at"]
    search_fields = ["provider_order_id", "provider_payment_id", "idempotency_key", "user__username"]
    readonly_fields = ["id", "raw_response", "created_at", "updated_at"]


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ["event_id", "provider", "event_type", "received_at", "processed_at"]
    list_filter = ["provider", "event_type", "received_at"]
    search_fields = ["event_id", "event_type"]
    readonly_fields = ["payload", "received_at", "processed_at"]


@admin.register(EmailDeliveryLog)
class EmailDeliveryLogAdmin(admin.ModelAdmin):
    list_display = ["booking", "recipient", "status", "attempts", "created_at", "updated_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["recipient", "booking__payment_id", "booking__movie__name"]
    readonly_fields = ["created_at", "updated_at", "last_error"]
