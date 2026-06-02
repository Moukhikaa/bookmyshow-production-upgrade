# Production Upgrade Notes

## Existing Architecture Impact

The original project had a clean small Django shape: `movies` owns movies, theaters, seats, and bookings; `users` owns authentication and profiles; templates render the booking flow. That structure is preserved.

The main limitations were:

- Seat booking used `seat.is_booked` checks in a loop, which is vulnerable to simultaneous requests.
- Payments were missing, so booking confirmation happened before money movement.
- Movie metadata was text-heavy and not filter-optimized.
- Analytics would require loading large querysets unless aggregation views were added.
- Secrets were hardcoded in `settings.py`.
- Email confirmation was synchronous/absent.

## Refactoring Strategy

The upgrade adds a service layer instead of growing `views.py`:

- `movies/services.py` contains reservation, payment, filtering, booking, and analytics orchestration.
- `movies/validators.py` and `movies/utils.py` isolate YouTube parsing and safe embed URL generation.
- `movies/tasks.py` contains Celery tasks for email and reservation expiry.
- `movies/constants.py` centralizes lifecycle states.

## Database Changes

New models:

- `Genre`, `Language`
- `Reservation`, `ReservationSeat`
- `PaymentTransaction`
- `PaymentWebhookEvent`

Extended models:

- `Movie.trailer_url`, `Movie.genres`, `Movie.languages`, `Movie.is_active`
- `Booking.status`, `Booking.amount`, `Booking.payment_id`, `Booking.reservation`

Important indexes and constraints:

- Unique seat per theater: `(theater, seat_number)`
- Booking aggregation indexes on user/time, movie/status, theater/status, booked_at
- Reservation expiry index on `(status, expires_at)`
- Payment idempotency and provider IDs are unique
- Webhook event IDs are unique for duplicate-event protection

## Concurrency Model

Seat reservation uses pessimistic locking:

- Expired locks are marked `EXPIRED`.
- Selected `Seat` rows are locked with `select_for_update()`.
- Active `ReservationSeat` rows are checked inside the same transaction.
- Booking confirmation locks the reservation and seats again before marking seats booked.

On PostgreSQL this gives strong consistency under concurrent requests. SQLite keeps the same code path for local development, but row-level locking is only fully effective after migrating to PostgreSQL.

## Payment Security

Razorpay is integrated through `PaymentService`:

- Orders are created server-side.
- Checkout signatures are verified server-side.
- Webhooks verify `X-Razorpay-Signature`.
- Webhook payloads are deduplicated by provider event ID or payload hash.
- Confirmation is idempotent: already-captured payments return existing bookings.
- Frontend callbacks alone are never trusted.

Secrets must come from environment variables:

- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`

## Trailer Security

Trailer URLs are validated with URL parsing, not raw HTML. Only YouTube hostnames and valid video IDs are accepted. Templates render only a generated `youtube-nocookie.com/embed/...` URL with:

- `loading="lazy"`
- `sandbox`
- restricted `allow` features
- no user-provided iframe HTML

## Filtering and Analytics

Filtering is server-side and paginated. Movies use indexed fields, M2M relations, `prefetch_related()`, and database-level counts.

Analytics uses ORM aggregation only:

- `Count`
- `Sum`
- `TruncDate`
- `TruncWeek`
- `TruncMonth`
- `ExtractHour`

Dashboard data is cached for five minutes and protected by Django staff access.

## Async Email and Expiry Workers

Celery tasks:

- `send_booking_confirmation_email`
- `expire_stale_reservations`

Use Redis in production:

```bash
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

Workers:

```bash
celery -A bookmyseat worker -l info
celery -A bookmyseat beat -l info
```

## Deployment Notes

Install dependencies:

```bash
pip install -r requirements.txt
```

Run migrations:

```bash
python manage.py migrate
```

Recommended production environment:

- PostgreSQL for row-level locks and reliable concurrent booking.
- Redis for Celery and cache.
- HTTPS-only cookies and secure proxy settings at the hosting layer.
- SMTP credentials in environment variables.
- Razorpay webhook configured to `/movies/payment/webhook/razorpay/`.
