"""Study Planner — orchestrator agent, build last."""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from agents.base import Agent
from backend.db import models
from backend.db.session import session

log = structlog.get_logger()


class StudyPlanner(Agent):
    name = "study_planner"
    subscribes_to = [
        "course.upload.new",
        "deadline.risk.escalated",
        "calendar.sync.complete",
        "social.events.ranked",
        "approval.granted",
        "schedule.morning",
    ]

    async def handle(self, event: dict[str, Any]) -> None:
        await self.log_event(
            type="study_planner.handle.start",
            payload={"trigger": event["type"]},
        )

        if event["type"] == "schedule.morning":
            await self._morning_briefing()
        elif event["type"] == "approval.granted":
            if event["payload"].get("agent") == self.name:
                await self._execute_plan(event["payload"])
        else:
            await self._replan(event)

    async def _replan(self, trigger_event: dict[str, Any]) -> None:
        """Gather all state and generate a new study plan using Opus."""
        context = await self._gather_context()

        await self.log_event(
            type="study_planner.replan.start",
            payload={
                "trigger": trigger_event["type"],
                "deadline_count": len(context["deadlines"]),
                "event_count": len(context["calendar_events"]),
            },
        )

        from backend.bedrock.claude import opus

        plan_text = await opus(
            system=(
                "You are a study planner for a TUM university student. "
                "Generate a concrete, time-blocked study plan that balances "
                "academic deadlines, lecture schedules, and social life. "
                "Prioritize by miss-probability: high-risk deadlines first. "
                "Output JSON with: {\"blocks\": [{\"start\": \"ISO8601\", "
                "\"end\": \"ISO8601\", \"activity\": \"...\", "
                "\"priority\": \"high|medium|low\", \"rationale\": \"...\"}], "
                "\"summary\": \"...\"}"
            ),
            prompt=json.dumps(context, default=str),
        )

        try:
            plan_data = json.loads(plan_text)
        except json.JSONDecodeError:
            plan_data = {"raw": plan_text, "blocks": []}

        async with session() as db:
            users = (await db.execute(select(models.User))).scalars().all()
            user = users[0] if users else None

        if user:
            async with session() as db:
                plan = models.Plan(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    content=plan_data,
                    rationale=plan_data.get("summary", ""),
                )
                db.add(plan)

        await self.publish(
            "plan.generated",
            {
                "trigger": trigger_event["type"],
                "block_count": len(plan_data.get("blocks", [])),
                "summary": plan_data.get("summary", ""),
            },
        )

    async def _gather_context(self) -> dict[str, Any]:
        """Pull all relevant state from Postgres for the planning prompt."""
        async with session() as db:
            deadlines = (
                (await db.execute(select(models.Deadline).where(models.Deadline.submitted == False)))
                .scalars()
                .all()
            )
            uploads = (
                (await db.execute(select(models.Upload).order_by(models.Upload.uploaded_at.desc()).limit(20)))
                .scalars()
                .all()
            )
            social = (
                (await db.execute(select(models.SocialEvent).order_by(models.SocialEvent.relevance_score.desc().nullslast()).limit(10)))
                .scalars()
                .all()
            )
            latest_plan = (
                (await db.execute(select(models.Plan).order_by(models.Plan.created_at.desc()).limit(1)))
                .scalars()
                .first()
            )

        return {
            "deadlines": [
                {
                    "title": d.title,
                    "due_at": d.due_at.isoformat(),
                    "weight": d.weight,
                    "miss_probability": d.miss_probability,
                    "miss_rationale": d.miss_rationale,
                }
                for d in deadlines
            ],
            "recent_uploads": [
                {
                    "filename": u.filename,
                    "summary": u.summary,
                    "concepts": u.concepts,
                }
                for u in uploads
            ],
            "social_events": [
                {
                    "title": s.title,
                    "starts_at": s.starts_at.isoformat(),
                    "location": s.location,
                    "relevance_score": s.relevance_score,
                }
                for s in social
            ],
            "calendar_events": [],
            "previous_plan_summary": latest_plan.rationale if latest_plan else None,
        }

    async def _morning_briefing(self) -> None:
        """Generate the daily morning briefing using Nova Pro."""
        context = await self._gather_context()

        await self.log_event(
            type="study_planner.briefing.start",
            payload={},
        )

        from backend.bedrock.nova import generate

        briefing = await generate(
            prompt=(
                f"Generate a concise morning briefing for a TUM student. "
                f"Today's context:\n{json.dumps(context, default=str)}\n\n"
                f"Cover: upcoming deadlines, today's schedule, "
                f"any interesting social events. Keep it under 200 words."
            ),
        )

        await self.publish(
            "briefing.generated",
            {"text": briefing},
        )

    async def _execute_plan(self, payload: dict[str, Any]) -> None:
        await self.log_event(
            type="study_planner.execute_plan.start",
            payload={"approval_id": payload.get("approval_id")},
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(StudyPlanner().run())
