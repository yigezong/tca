"""Coarse wall-clock coordination metrics derived from append-only events."""

from __future__ import annotations

from datetime import datetime
from statistics import median
from .domain import SubgoalEvent


def claim_metrics(
    events: list[dict], subgoal_events: list[SubgoalEvent] | None = None,
) -> dict[str, object]:
    first: dict[str, datetime] = {}
    state: str | None = None
    state_since: datetime | None = None
    seconds = {"coordination": 0.0, "post_approval_wait": 0.0,
               "execution": 0.0, "integration": 0.0}
    phase_for = {
        "received": "coordination", "assessing": "coordination",
        "waiting": "coordination", "needs_human": "coordination",
        "approved": "post_approval_wait",
        "approved_with_constraints": "post_approval_wait",
        "executing": "execution", "completed": "integration",
    }
    revisions = 0
    reassessments = 0
    for event in events:
        try:
            at = datetime.fromisoformat(event["created_at"])
        except (KeyError, ValueError, TypeError):
            continue
        if event["event_type"] == "claim_submitted":
            revisions += 1
            first.setdefault("submitted", at)
            if state is None:
                state, state_since = "received", at
        elif event["event_type"] == "state_transition":
            target = event["payload"].get("to")
            if state_since and state in phase_for:
                seconds[phase_for[state]] += max(0.0, (at - state_since).total_seconds())
            if target == "executing":
                first.setdefault("executing", at)
            elif target == "completed":
                first.setdefault("completed", at)
            elif target == "integrated":
                first.setdefault("integrated", at)
            if target == "needs_human" or (target == "assessing" and state in {"approved", "approved_with_constraints"}):
                reassessments += 1
            state, state_since = target, at

    rework_started: dict[str, datetime] = {}
    rework_seconds = 0.0
    rework_count = 0
    for event in subgoal_events or []:
        try:
            at = datetime.fromisoformat(event.created_at)
        except (ValueError, TypeError):
            continue
        if event.event_type.value == "rework_started":
            rework_count += 1
            rework_started[event.subgoal_id] = at
        elif event.event_type.value == "rework_finished":
            started = rework_started.pop(event.subgoal_id, None)
            if started:
                rework_seconds += max(0.0, (at - started).total_seconds())

    def duration(phase: str, ready: bool) -> float | None:
        return round(seconds[phase], 3) if ready else None

    submitted = first.get("submitted")
    return {
        "submitted_at": submitted.isoformat() if submitted else None,
        "integrated_at": first["integrated"].isoformat() if "integrated" in first else None,
        "coordination_seconds": duration("coordination", "executing" in first),
        "post_approval_wait_seconds": duration("post_approval_wait", "executing" in first),
        "execution_seconds": duration("execution", "completed" in first),
        "integration_seconds": duration("integration", "integrated" in first),
        "end_to_end_seconds": (round(max(0.0, (first["integrated"] - submitted).total_seconds()), 3)
                               if submitted and "integrated" in first else None),
        "claim_revisions": revisions,
        "reassessment_count": reassessments,
        "reported_rework_count": rework_count,
        "reported_rework_seconds": round(rework_seconds, 3),
    }


def project_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    completed = [r for r in rows if r["execution_seconds"] is not None]
    project_elapsed: float | None = None
    if rows and all(r["submitted_at"] and r["integrated_at"] for r in rows):
        first_submission = min(datetime.fromisoformat(str(r["submitted_at"])) for r in rows)
        last_integration = max(datetime.fromisoformat(str(r["integrated_at"])) for r in rows)
        project_elapsed = round(max(0.0, (last_integration - first_submission).total_seconds()), 3)
    def med(key: str) -> float | None:
        values = [float(r[key]) for r in rows if r[key] is not None]
        return round(median(values), 3) if values else None
    return {
        "claims": len(rows),
        "completed": len(completed),
        "project_elapsed_seconds": project_elapsed,
        "median_coordination_seconds": med("coordination_seconds"),
        "median_execution_seconds": med("execution_seconds"),
        "median_integration_seconds": med("integration_seconds"),
        "median_reported_rework_seconds": med("reported_rework_seconds"),
        "median_end_to_end_seconds": med("end_to_end_seconds"),
        "total_reassessment_count": sum(int(r["reassessment_count"]) for r in rows),
        "total_reported_rework_count": sum(int(r["reported_rework_count"]) for r in rows),
        "total_reported_rework_seconds": round(
            sum(float(r["reported_rework_seconds"]) for r in rows), 3
        ),
        "per_claim": rows,
    }
