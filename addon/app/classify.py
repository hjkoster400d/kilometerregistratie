"""Trip classification rule (pure, no I/O so it is trivially testable).

Rule (as specified by the user):
    A trip is BUSINESS if its start zone is a work zone OR its end zone is a work
    zone. Everything else is PRIVATE. This makes home->work and work->home both
    business, which is the intended behaviour.

A manual override always wins and is handled at the storage/API layer, not here.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .storage import BUSINESS, PRIVATE


def classify(
    start_zone: Optional[str],
    end_zone: Optional[str],
    work_zones: Iterable[str],
) -> str:
    """Return 'business' if either endpoint is in a work zone, else 'private'."""
    work = set(work_zones)
    if (start_zone in work) or (end_zone in work):
        return BUSINESS
    return PRIVATE
