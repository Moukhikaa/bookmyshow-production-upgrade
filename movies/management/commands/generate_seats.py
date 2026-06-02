from django.core.management.base import BaseCommand, CommandError

from movies.models import Seat, Theater


class Command(BaseCommand):
    help = "Generate seats for a theater without overwriting existing booked seats."

    def add_arguments(self, parser):
        parser.add_argument("theater_id", type=int)
        parser.add_argument("--rows", default="A,B,C,D,E,F,G,H", help="Comma-separated row labels.")
        parser.add_argument("--cols", type=int, default=12, help="Number of seats per row.")
        parser.add_argument(
            "--replace-unbooked",
            action="store_true",
            help="Delete existing unbooked seats for this theater before generating.",
        )

    def handle(self, *args, **options):
        try:
            theater = Theater.objects.get(id=options["theater_id"])
        except Theater.DoesNotExist as exc:
            raise CommandError(f"Theater {options['theater_id']} does not exist.") from exc

        if options["replace_unbooked"]:
            Seat.objects.filter(theater=theater, is_booked=False).delete()

        rows = [row.strip().upper() for row in options["rows"].split(",") if row.strip()]
        if not rows:
            raise CommandError("At least one row is required.")

        created = 0
        skipped = 0
        for row in rows:
            for col in range(1, options["cols"] + 1):
                _, was_created = Seat.objects.get_or_create(
                    theater=theater,
                    seat_number=f"{row}{col}",
                    defaults={"is_booked": False},
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Generated seats for {theater.name}: {created} created, {skipped} already existed."
            )
        )
