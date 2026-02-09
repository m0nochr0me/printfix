"""Effort level configuration and model routing."""

from dataclasses import dataclass

from app.schema.job import EffortLevel

__all__ = ("EffortConfig", "get_effort_config")


@dataclass(frozen=True)
class EffortConfig:
    visual_model: str
    use_claude_structural: bool
    claude_model: str | None
    max_pages_sampled: int | None  # None = all pages
    page_batch_size: int  # images per Gemini call
    use_ai_merge: bool  # True = Claude merge, False = rule-based
    # Orchestration (Phase 4)
    max_fix_iterations: int = 1
    use_ai_planning: bool = False  # True = AI selects fixes; False = rule-based
    orchestration_model: str | None = None  # Gemini or Claude model for planning


EFFORT_CONFIGS: dict[EffortLevel, EffortConfig] = {
    EffortLevel.quick: EffortConfig(
        visual_model="gemini-2.0-flash-lite",
        use_claude_structural=False,
        claude_model=None,
        max_pages_sampled=10,
        page_batch_size=4,
        use_ai_merge=False,
        max_fix_iterations=1,
        use_ai_planning=False,
        orchestration_model="gemini-2.0-flash-lite",
    ),
    EffortLevel.standard: EffortConfig(
        visual_model="gemini-2.0-flash",
        use_claude_structural=False,
        claude_model=None,
        max_pages_sampled=None,
        page_batch_size=4,
        use_ai_merge=False,
        max_fix_iterations=3,
        use_ai_planning=False,
        orchestration_model="gemini-2.0-flash",
    ),
    EffortLevel.thorough: EffortConfig(
        visual_model="gemini-2.0-flash",
        use_claude_structural=True,
        claude_model=None,  # resolved at runtime from settings
        max_pages_sampled=None,
        page_batch_size=2,
        use_ai_merge=True,
        max_fix_iterations=5,
        use_ai_planning=True,
        orchestration_model=None,  # resolved at runtime → Claude from settings
    ),
}


def get_effort_config(effort: EffortLevel) -> EffortConfig:
    return EFFORT_CONFIGS[effort]
