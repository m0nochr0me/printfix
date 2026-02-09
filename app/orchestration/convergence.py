"""
Convergence detection — decides when the fix loop should stop.
"""

from __future__ import annotations

from app.schema.orchestration import ConvergenceState

__all__ = ("should_stop",)


def should_stop(
    states: list[ConvergenceState],
    max_iterations: int,
    *,
    fallback_available: bool = False,
) -> tuple[bool, str]:
    """
    Determine whether the fix loop should stop.

    When ``fallback_available`` is True, the loop is allowed to continue
    past "all fixes failed" and "issues stalled" conditions so that
    PDF fallback tools can be attempted in the next iteration.

    Returns (should_stop, reason).
    """
    if not states:
        return False, ""

    current = states[-1]

    # 1. Max iterations reached — always stop
    if current.iteration >= max_iterations:
        return True, f"max iterations reached ({max_iterations})"

    # 2. No critical or warning issues remain → print-ready
    if current.issues_after == 0:
        return True, "all issues resolved"
    if current.critical_after == 0 and current.warning_after == 0:
        return True, "no critical or warning issues remain"

    # 3. All planned fixes failed → no progress possible
    #    UNLESS PDF fallback tools are available to try next
    if current.fixes_applied == 0 and current.fixes_failed > 0:
        if fallback_available:
            return False, ""
        return True, "all fixes failed, no progress possible"

    # 4. No fixes were planned (empty plan)
    if current.fixes_applied == 0 and current.fixes_failed == 0:
        return True, "no applicable fixes found"

    # 5. Issue count stalled or worsened (compare to previous iteration)
    #    UNLESS PDF fallback tools are available to try next
    if len(states) >= 2:
        prev = states[-2]
        if current.issues_after >= prev.issues_after:
            if fallback_available:
                return False, ""
            return True, (
                f"issues did not decrease "
                f"({prev.issues_after} → {current.issues_after})"
            )

    return False, ""
