"""AI client initialization."""

from google import genai

from app.core.config import settings

__all__ = (
    "ai_client",
    "extract_anthropic_text",
    "get_anthropic_client",
)

ai_client = genai.Client(api_key=settings.GOOGLE_API_KEY)

_anthropic_client = None


def get_anthropic_client():
    """Lazy-initialize the Anthropic client. Returns None if no API key configured."""
    global _anthropic_client  # noqa: PLW0603
    if _anthropic_client is None and settings.ANTHROPIC_API_KEY:
        import anthropic  # noqa: PLC0415

        _anthropic_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


def extract_anthropic_text(response) -> str:
    """Extract text from an Anthropic Messages API response."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
