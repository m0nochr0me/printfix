"""AI client initialization."""

from google import genai

from app.core.config import settings

__all__ = ("ai_client",)

ai_client = genai.Client(api_key=settings.GOOGLE_API_KEY)
