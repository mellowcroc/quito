from __future__ import annotations

from ..models import (
    GateDecision,
    GateResult,
    ReviewComment,
    Severity,
    VisualFinding,
)


def evaluate_gate(
    iteration: int,
    max_iterations: int,
    review_comments: list[ReviewComment],
    visual_findings: list[VisualFinding],
) -> GateResult:
    all_severities = [c.severity for c in review_comments] + [f.severity for f in visual_findings]

    critical = sum(1 for s in all_severities if s == Severity.CRITICAL)
    high = sum(1 for s in all_severities if s == Severity.HIGH)
    medium = sum(1 for s in all_severities if s == Severity.MEDIUM)

    if critical == 0 and high == 0:
        return GateResult(
            decision=GateDecision.PROCEED_TO_BUGBASH,
            reason=f"No critical/high issues remain ({medium} medium, iteration {iteration})",
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            iteration=iteration,
        )

    if iteration >= max_iterations:
        return GateResult(
            decision=GateDecision.HALT,
            reason=f"Max iterations ({max_iterations}) reached with {critical} critical, {high} high issues remaining",
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            iteration=iteration,
        )

    return GateResult(
        decision=GateDecision.LOOP_BACK,
        reason=f"Iteration {iteration}: {critical} critical, {high} high issues — looping back",
        critical_count=critical,
        high_count=high,
        medium_count=medium,
        iteration=iteration,
    )
