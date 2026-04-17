"""Miss-probability: logistic heuristic + LLM rationale."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class MissRisk:
    probability: float
    level: str
    rationale: str


def compute_miss_probability(
    due_at: datetime,
    submitted: bool,
    weight: float,
    now: datetime | None = None,
) -> MissRisk:
    if submitted:
        return MissRisk(probability=0.0, level="low", rationale="Already submitted.")

    now = now or datetime.now(timezone.utc)
    hours_left = max((due_at - now).total_seconds() / 3600, 0)

    # Logistic curve: prob = 1 / (1 + e^(k*(hours - midpoint)))
    # High weight assignments get a tighter curve (earlier escalation)
    midpoint = 72 - (weight * 40)
    k = 0.08 + (weight * 0.04)
    raw = 1.0 / (1.0 + math.exp(k * (hours_left - midpoint)))

    prob = round(min(max(raw, 0.0), 1.0), 3)

    if prob >= 0.7:
        level = "high"
    elif prob >= 0.35:
        level = "medium"
    else:
        level = "low"

    hours_display = int(hours_left)
    rationale = (
        f"{hours_display}h left, weight {weight:.0%}, "
        f"miss probability {prob:.0%} ({level})"
    )

    return MissRisk(probability=prob, level=level, rationale=rationale)
