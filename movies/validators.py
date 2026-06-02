from urllib.parse import parse_qs, urlparse

from django.core.exceptions import ValidationError


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


def extract_youtube_video_id(url):
    if not url:
        return None

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in YOUTUBE_HOSTS or parsed.scheme not in {"http", "https"}:
        return None

    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [None])[0]
    elif parsed.path.startswith("/embed/"):
        video_id = parsed.path.split("/embed/", 1)[1].split("/")[0]
    elif parsed.path.startswith("/shorts/"):
        video_id = parsed.path.split("/shorts/", 1)[1].split("/")[0]
    else:
        return None

    if not video_id or len(video_id) > 32:
        return None

    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if any(char not in allowed for char in video_id):
        return None
    return video_id


def validate_youtube_url(value):
    if value and not extract_youtube_video_id(value):
        raise ValidationError("Enter a valid YouTube trailer URL.")
