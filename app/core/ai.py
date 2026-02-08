"""AI client initialization."""

from google import genai

from app.core.config import settings

__all__ = ("ai_client", "get_anthropic_client")

ai_client = genai.Client(api_key=settings.GOOGLE_API_KEY)

_anthropic_client = None


def get_anthropic_client():
    """Lazy-initialize the Anthropic client. Returns None if no API key configured."""
    global _anthropic_client
    if _anthropic_client is None and settings.ANTHROPIC_API_KEY:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client
