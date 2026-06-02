from urllib.parse import urlencode

from .validators import extract_youtube_video_id


def build_youtube_embed_url(url):
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return ""
    query = urlencode(
        {
            "rel": "0",
            "modestbranding": "1",
            "playsinline": "1",
        }
    )
    return f"https://www.youtube-nocookie.com/embed/{video_id}?{query}"


def build_youtube_thumbnail_url(url):
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return ""
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
