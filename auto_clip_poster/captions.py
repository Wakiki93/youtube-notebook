"""Build the Instagram caption from AutoClip's post copy."""
from __future__ import annotations

import re

from .clipper import Clip

IG_CAPTION_LIMIT = 2200  # Instagram's caption limit (characters)
IG_HASHTAG_LIMIT = 30  # Instagram rejects captions with more than 30 hashtags


def _hashtag(tag: str) -> str:
    cleaned = re.sub(r"[^\w]", "", tag.replace(" ", ""), flags=re.UNICODE)
    return f"#{cleaned}" if cleaned else ""


def build_caption(clip: Clip, footer: str = "", max_hashtags: int = 10) -> str:
    limit = min(max_hashtags, IG_HASHTAG_LIMIT)
    tags: list[str] = []
    for tag in clip.tags:
        h = _hashtag(tag)
        if h and h.lower() not in {t.lower() for t in tags}:
            tags.append(h)
        if len(tags) >= limit:
            break
    credit = clip.credit.strip()
    if credit and not credit.lower().startswith(("credit", "via", "source", "🎥")):
        credit = f"Credit: {credit}"
    parts = [clip.title, clip.description, credit, footer.strip(), " ".join(tags)]
    caption = "\n\n".join(p.strip() for p in parts if p and p.strip())
    if len(caption) > IG_CAPTION_LIMIT:
        # Trim the description first so the credit, footer and hashtags survive.
        tail = "\n\n".join(p.strip() for p in [credit, footer.strip(), " ".join(tags)] if p and p.strip())
        room = IG_CAPTION_LIMIT - len(tail) - len(clip.title) - 6
        description = clip.description[: max(room, 0)].rstrip()
        if description and len(description) < len(clip.description):
            description = description[:-1].rstrip() + "…"
        caption = "\n\n".join(p for p in [clip.title.strip(), description, tail] if p)[:IG_CAPTION_LIMIT]
    return caption
