from __future__ import annotations

from ..models import ReviewComment, Spec
from ..stages.base import ReviewStage


class MultiReview(ReviewStage):
    name = "multi"

    def __init__(self, reviewers: list[ReviewStage]):
        if not reviewers:
            raise ValueError("MultiReview requires at least one reviewer")
        self.reviewers = reviewers
        self.name = "+".join(r.name for r in reviewers)

    def review(
        self,
        code: dict[str, str],
        spec: Spec,
        plan: str = "",
    ) -> list[ReviewComment]:
        all_comments: list[ReviewComment] = []
        for reviewer in self.reviewers:
            comments = reviewer.review(code, spec, plan)
            for c in comments:
                c.comment = f"[{reviewer.name}] {c.comment}"
            all_comments.extend(comments)
        return all_comments
