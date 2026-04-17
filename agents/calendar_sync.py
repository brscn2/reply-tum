"""Calendar Sync agent — syncs TUMonline + Google Calendar."""

from __future__ import annotations

import os
from typing import Any

import structlog
from sqlalchemy import select

from agents.base import Agent
from backend.db import models
from backend.db.session import session

log = structlog.get_logger()

MOCK_MODE = os.getenv("SCHATTEN_INTEGRATION_MODE", "mock") == "mock"


class CalendarSync(Agent):
    name = "calendar_sync"
    subscribes_to = [
        "course.upload.new",
        "deadline.risk.escalated",
        "approval.granted",
        "schedule.poll.calendar",
    ]
    poll_interval_seconds = 900

    async def handle(self, event: dict[str, Any]) -> None:
        await self.log_event(
            type="calendar_sync.handle.start",
            payload={"trigger": event["type"]},
        )

        if event["type"] == "approval.granted":
            await self._execute_approved_action(event["payload"])
        elif event["type"] == "deadline.risk.escalated":
            await self._propose_study_block(event["payload"])
        elif event["type"] == "course.upload.new":
            await self._propose_review_session(event["payload"])
        else:
            await self._sync_ical()

    async def poll(self) -> None:
        await self.log_event(
            type="calendar_sync.poll.start",
            payload={},
        )
        await self._sync_ical()

    async def _sync_ical(self) -> None:
        """Fetch TUMonline iCal and detect changes vs Google Calendar."""
        from integrations.tumonline_ical import fetch_ical_events

        ical_events = await fetch_ical_events()

        await self.log_event(
            type="calendar_sync.ical.fetched",
            payload={"event_count": len(ical_events)},
        )

        await self.publish(
            "calendar.sync.complete",
            {"event_count": len(ical_events), "events": ical_events},
        )

    async def _propose_study_block(self, payload: dict[str, Any]) -> None:
        """Deadline is at risk — propose a study block on Google Calendar."""
        await self.log_event(
            type="calendar_sync.propose_study_block.start",
            payload={"deadline_id": payload["deadline_id"]},
        )

        from backend.bedrock.claude import sonnet

        result = await sonnet(
            system="You help students plan study blocks in their calendar.",
            prompt=(
                f"A deadline is at risk:\n"
                f"Title: {payload['title']}\n"
                f"Due: {payload['due_at']}\n"
                f"Miss probability: {payload['miss_probability']:.0%}\n"
                f"Rationale: {payload['rationale']}\n\n"
                f"Suggest a study block: start time, duration, and description. "
                f"Respond as JSON: "
                f'{{\"summary\": \"...\", \"start\": \"ISO8601\", \"duration_minutes\": N}}'
            ),
        )

        async with session() as db:
            users = (
                (await db.execute(select(models.User))).scalars().all()
            )
            user = users[0] if users else None

        if user:
            import uuid

            approval = models.Approval(
                id=uuid.uuid4(),
                user_id=user.id,
                agent=self.name,
                action_type="calendar.add_study_block",
                rendered_text=f"Add study block for '{payload['title']}'?\n\n{result}",
                status="pending",
            )
            async with session() as db:
                db.add(approval)

            await self.publish(
                "approval.requested",
                {
                    "approval_id": str(approval.id),
                    "agent": self.name,
                    "action_type": "calendar.add_study_block",
                    "rendered_text": approval.rendered_text,
                },
            )

    async def _propose_review_session(self, payload: dict[str, Any]) -> None:
        """New upload detected — propose a review session."""
        await self.log_event(
            type="calendar_sync.propose_review.start",
            payload={"filename": payload["filename"]},
        )

        async with session() as db:
            users = (
                (await db.execute(select(models.User))).scalars().all()
            )
            user = users[0] if users else None

        if user:
            import uuid

            text = (
                f"New material in {payload['course_name']}: {payload['filename']}\n"
                f"Summary: {payload['summary']}\n\n"
                f"Add a 30-min review session to your calendar?"
            )
            approval = models.Approval(
                id=uuid.uuid4(),
                user_id=user.id,
                agent=self.name,
                action_type="calendar.add_review_session",
                rendered_text=text,
                status="pending",
            )
            async with session() as db:
                db.add(approval)

            await self.publish(
                "approval.requested",
                {
                    "approval_id": str(approval.id),
                    "agent": self.name,
                    "action_type": "calendar.add_review_session",
                    "rendered_text": text,
                },
            )

    async def _execute_approved_action(self, payload: dict[str, Any]) -> None:
        """An approval was granted — execute the calendar write."""
        if payload.get("agent") != self.name:
            return

        await self.log_event(
            type="calendar_sync.execute.start",
            payload={"approval_id": payload["approval_id"]},
        )

        from integrations.gcal_client import create_event

        await create_event(payload)

        await self.publish(
            "calendar.event.created",
            {"approval_id": payload["approval_id"]},
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(CalendarSync().run())
