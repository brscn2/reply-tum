"""Deadline Sentinel agent — tracks assignment deadlines and risk."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select

from agents.base import Agent
from backend.db import models
from backend.db.session import session
from models.miss_probability import compute_miss_probability

log = structlog.get_logger()

ESCALATION_THRESHOLD = 0.6


class DeadlineSentinel(Agent):
    name = "deadline_sentinel"
    subscribes_to = [
        "course.upload.new",
        "calendar.sync.complete",
        "schedule.poll.deadlines",
    ]
    poll_interval_seconds = 600

    async def handle(self, event: dict[str, Any]) -> None:
        await self.log_event(
            type="deadline_sentinel.handle.start",
            payload={"trigger": event["type"]},
        )

        if event["type"] == "course.upload.new":
            await self._recompute_for_course(event["payload"]["course_id"])
        else:
            await self._scan_all_deadlines()

    async def poll(self) -> None:
        await self.log_event(
            type="deadline_sentinel.poll.start",
            payload={},
        )
        await self._scan_all_deadlines()

    async def _scan_all_deadlines(self) -> None:
        async with session() as db:
            deadlines = (
                (await db.execute(select(models.Deadline).where(models.Deadline.submitted == False)))
                .scalars()
                .all()
            )

        for dl in deadlines:
            await self._evaluate_deadline(dl)

    async def _recompute_for_course(self, course_id: str) -> None:
        async with session() as db:
            deadlines = (
                (
                    await db.execute(
                        select(models.Deadline).where(
                            models.Deadline.course_id == course_id,
                            models.Deadline.submitted == False,
                        )
                    )
                )
                .scalars()
                .all()
            )

        for dl in deadlines:
            await self._evaluate_deadline(dl)

    async def _evaluate_deadline(self, deadline: models.Deadline) -> None:
        risk = compute_miss_probability(
            due_at=deadline.due_at,
            submitted=deadline.submitted,
            weight=deadline.weight,
        )

        await self.log_event(
            type="deadline_sentinel.evaluate",
            payload={
                "deadline_id": str(deadline.id),
                "title": deadline.title,
                "probability": risk.probability,
                "level": risk.level,
            },
        )

        rationale = await self._generate_rationale(deadline, risk)

        async with session() as db:
            dl = await db.get(models.Deadline, deadline.id)
            if dl:
                dl.miss_probability = risk.probability
                dl.miss_rationale = rationale

        if risk.probability >= ESCALATION_THRESHOLD:
            await self.publish(
                "deadline.risk.escalated",
                {
                    "deadline_id": str(deadline.id),
                    "course_id": str(deadline.course_id),
                    "title": deadline.title,
                    "due_at": deadline.due_at.isoformat(),
                    "miss_probability": risk.probability,
                    "level": risk.level,
                    "rationale": rationale,
                },
            )

    async def _generate_rationale(
        self, deadline: models.Deadline, risk: Any
    ) -> str:
        await self.log_event(
            type="deadline_sentinel.rationale.start",
            payload={"deadline_id": str(deadline.id)},
        )

        from backend.bedrock.claude import sonnet

        now = datetime.now(timezone.utc)
        hours_left = max((deadline.due_at - now).total_seconds() / 3600, 0)

        result = await sonnet(
            system="You write one-sentence risk assessments for a student's deadlines.",
            prompt=(
                f"Deadline: {deadline.title}\n"
                f"Hours remaining: {int(hours_left)}\n"
                f"Weight: {deadline.weight:.0%}\n"
                f"Miss probability: {risk.probability:.0%}\n\n"
                f"Write ONE sentence explaining the risk level to the student."
            ),
        )
        return result


if __name__ == "__main__":
    import asyncio

    asyncio.run(DeadlineSentinel().run())
