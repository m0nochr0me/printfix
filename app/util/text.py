"""
Text cleanup utils
"""

import re
import unicodedata
from urllib.parse import unquote

from app.core.cache import cache, make_cache_key


async def slight_cleanup_text(text: str) -> str:
    """
    Cleans up the text by normalizing Unicode characters and unquoting URL-encoded strings.
    """

    cache_key = make_cache_key("slight_cleanup_text", text)
    cached_result = await cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    text = unquote(text)
    await cache.set(cache_key, text.strip())
    return text.strip()


async def full_cleanup_text(text: str) -> str:
    """
    Preprocesses the text by removing unwanted characters and normalizing whitespace.
    """

    cache_key = make_cache_key("full_cleanup_text", text)
    cached_result = await cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    text = await slight_cleanup_text(text)
    text = re.sub(r"[!\"#$%&'()*+,-./:;<=>?@\[\\\]^_`{|}~]", "", text)
    text = re.sub(r"\s+", " ", text)
    await cache.set(cache_key, text.strip())
    return text.strip()
