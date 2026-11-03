from dataclasses import dataclass


@dataclass(frozen=True)
class ReservationResult:
    """Prototype result; the reservation contract is intentionally unfinished."""

    ok: bool
