from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from django.conf import settings


class Command(BaseCommand):
    help = "Create or update the deployment admin user from environment variables."

    def handle(self, *args, **options):
        username = getattr(settings, "ADMIN_USERNAME", None)
        email = getattr(settings, "ADMIN_EMAIL", None)
        password = getattr(settings, "ADMIN_PASSWORD", None)

        if not username or not email or not password:
            raise CommandError(
                "ADMIN_USERNAME, ADMIN_EMAIL, and ADMIN_PASSWORD are required "
                "when CREATE_DEPLOY_ADMIN=True."
            )

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save(update_fields=["email", "is_staff", "is_superuser", "is_active", "password"])

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} deployment admin user: {username}"))
