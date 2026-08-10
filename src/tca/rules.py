from __future__ import annotations

from typing import Sequence

from .domain import Claim, ClaimRecord, ClaimState, Decision


def _path_root(pattern: str) -> str:
    return pattern.replace("\\", "/").rstrip("/*")


def paths_overlap(left: str, right: str) -> bool:
    left_root = _path_root(left)
    right_root = _path_root(right)
    return (
        left_root == right_root
        or left_root.startswith(right_root + "/")
        or right_root.startswith(left_root + "/")
    )


class BasicRuleChecker:
    """C-owned deterministic checks. This is not M and performs no semantic judgment."""

    def check(
        self, claim: Claim, active_claims: Sequence[ClaimRecord]
    ) -> Decision | None:
        dependencies = [
            record.claim.claim_id
            for record in active_claims
            if record.claim.claim_id in claim.dependencies
            and record.state is not ClaimState.COMPLETED
        ]
        if dependencies:
            return Decision(
                state=ClaimState.WAITING,
                reason="declared dependencies are still active",
                wait_for=tuple(dependencies),
            )

        conflicts: list[str] = []
        constraints: list[str] = []
        for record in active_claims:
            if record.state is ClaimState.COMPLETED:
                continue
            overlap = any(
                paths_overlap(candidate, occupied)
                for candidate in claim.planned_paths
                for occupied in record.claim.planned_paths
            )
            if overlap:
                conflicts.append(record.claim.claim_id)
                constraints.extend(record.claim.planned_paths)

        if conflicts:
            return Decision(
                state=ClaimState.WAITING,
                reason="planned paths overlap active claims",
                constraints=tuple(sorted(set(constraints))),
                wait_for=tuple(conflicts),
                conflicts_with=tuple(conflicts),
            )

        return None
