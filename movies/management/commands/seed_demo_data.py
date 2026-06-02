from decimal import Decimal
from datetime import datetime, time, timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from movies.models import Genre, Language, Movie, Theater


DEMO_MOVIES = [
    {
        "name": "The Avengers",
        "image": "movies/demo_posters/the_avengers.svg",
        "rating": "8.0",
        "cast": "Robert Downey Jr., Chris Evans, Scarlett Johansson, Mark Ruffalo",
        "description": "Earth's mightiest heroes unite to stop Loki and an alien army from enslaving humanity.",
        "trailer_url": "https://www.youtube.com/watch?v=eOrNdBpGMv8",
        "genres": ["Action", "Adventure", "Sci-Fi"],
        "languages": ["English"],
    },
    {
        "name": "Inception",
        "image": "movies/demo_posters/inception.svg",
        "rating": "8.8",
        "cast": "Leonardo DiCaprio, Joseph Gordon-Levitt, Elliot Page, Tom Hardy",
        "description": "A skilled thief enters dreams to plant an idea that could change everything.",
        "trailer_url": "https://www.youtube.com/watch?v=YoHD9XEInc0",
        "genres": ["Sci-Fi", "Thriller"],
        "languages": ["English"],
    },
    {
        "name": "Interstellar",
        "image": "movies/demo_posters/interstellar.svg",
        "rating": "8.7",
        "cast": "Matthew McConaughey, Anne Hathaway, Jessica Chastain, Michael Caine",
        "description": "Explorers travel through a wormhole in search of a future for humanity.",
        "trailer_url": "https://www.youtube.com/watch?v=zSWdZVtXT7E",
        "genres": ["Adventure", "Drama", "Sci-Fi"],
        "languages": ["English"],
    },
    {
        "name": "RRR",
        "image": "movies/demo_posters/rrr.svg",
        "rating": "8.0",
        "cast": "N. T. Rama Rao Jr., Ram Charan, Alia Bhatt, Ajay Devgn",
        "description": "Two legendary revolutionaries form a fierce bond in colonial India.",
        "trailer_url": "https://www.youtube.com/watch?v=f_vbAtFSEc0",
        "genres": ["Action", "Drama", "Historical"],
        "languages": ["Telugu", "Hindi"],
    },
    {
        "name": "Dangal",
        "image": "movies/demo_posters/dangal.svg",
        "rating": "8.3",
        "cast": "Aamir Khan, Fatima Sana Shaikh, Sanya Malhotra, Sakshi Tanwar",
        "description": "A former wrestler trains his daughters to become world-class champions.",
        "trailer_url": "https://www.youtube.com/watch?v=x_7YlGv9u1g",
        "genres": ["Biography", "Drama", "Sports"],
        "languages": ["Hindi"],
    },
    {
        "name": "Oppenheimer",
        "image": "movies/demo_posters/oppenheimer.svg",
        "rating": "8.4",
        "cast": "Cillian Murphy, Emily Blunt, Matt Damon, Robert Downey Jr.",
        "description": "The story of J. Robert Oppenheimer and the creation of the atomic bomb.",
        "trailer_url": "https://www.youtube.com/watch?v=uYPbbksJxIg",
        "genres": ["Biography", "Drama", "Historical"],
        "languages": ["English"],
    },
]


class Command(BaseCommand):
    help = "Seed clean demo movies, trailers, genres, languages, theaters, and seats."

    def handle(self, *args, **options):
        Movie.objects.exclude(name__in=[movie["name"] for movie in DEMO_MOVIES]).update(is_active=False)
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        week_dates = [week_start + timedelta(days=offset) for offset in range(7)]
        show_slots = [
            ("PVR Nexus Mall", time(18, 30)),
            ("INOX City Centre", time(21, 30)),
        ]

        for movie_data in DEMO_MOVIES:
            movie, _ = Movie.objects.update_or_create(
                name=movie_data["name"],
                defaults={
                    "image": movie_data["image"],
                    "rating": Decimal(movie_data["rating"]),
                    "cast": movie_data["cast"],
                    "description": movie_data["description"],
                    "trailer_url": movie_data["trailer_url"],
                    "is_active": True,
                },
            )
            movie.genres.set(
                [
                    Genre.objects.get_or_create(
                        name=name,
                        defaults={"slug": name.lower().replace(" ", "-")},
                    )[0]
                    for name in movie_data["genres"]
                ]
            )
            movie.languages.set(
                [
                    Language.objects.get_or_create(
                        name=name,
                        defaults={"code": name[:2].lower()},
                    )[0]
                    for name in movie_data["languages"]
                ]
            )
            deletable_theater_ids = list(Theater.objects.filter(
                movie=movie,
                booking__isnull=True,
                reservations__isnull=True,
            ).values_list("id", flat=True).distinct())
            Theater.objects.filter(id__in=deletable_theater_ids).delete()

            for day in week_dates:
                for theater_name, slot in show_slots:
                    show_time = timezone.make_aware(datetime.combine(day, slot))
                    if show_time <= timezone.now():
                        continue
                    theater = Theater.objects.create(
                        name=theater_name,
                        movie=movie,
                        time=show_time,
                    )
                    call_command(
                        "generate_seats",
                        theater.id,
                        rows="A,B,C,D,E,F,G,H",
                        cols=12,
                        replace_unbooked=True,
                        verbosity=0,
                    )

            if not Theater.objects.filter(movie=movie, time__gte=timezone.now()).exists():
                fallback_time = timezone.now() + timedelta(hours=2)
                theater = Theater.objects.create(
                    name=theater_name,
                    movie=movie,
                    time=fallback_time,
                )
                call_command(
                    "generate_seats",
                    theater.id,
                    rows="A,B,C,D,E,F,G,H",
                    cols=12,
                    replace_unbooked=True,
                    verbosity=0,
                )

        self.stdout.write(self.style.SUCCESS("Demo movies, trailers, shows, and seats are ready."))
