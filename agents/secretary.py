"""Secretary agent — STRETCH goal."""

from __future__ import annotations

import json
from typing import Any

import structlog

from agents.base import Agent
from backend.db import models
from backend.db.session import session

log = structlog.get_logger()


class Secretary(Agent):
    name = "secretary"
    subscribes_to = [
        "schedule.morning",
        "plan.generated",
        "briefing.generated",
    ]

    async def handle(self, event: dict[str, Any]) -> None:
        await self.log_event(
            type="secretary.handle.start",
            payload={"trigger": event["type"]},
        )

        if event["type"] == "briefing.generated":
            await self._format_and_send_briefing(event["payload"])
        elif event["type"] == "plan.generated":
            await self._notify_plan_ready(event["payload"])

    async def _format_and_send_briefing(self, payload: dict[str, Any]) -> None:
        """Format the morning briefing and send via Telegram."""
        await self.log_event(
            type="secretary.format_briefing.start",
            payload={},
        )

        from backend.bedrock.claude import sonnet

        formatted = await sonnet(
            system=(
                "You format morning briefings for Telegram. "
                "Use markdown formatting. Keep it friendly and concise."
            ),
            prompt=(
                f"Format this briefing for Telegram:\n\n{payload['text']}"
            ),
        )

        async with session() as db:
            from sqlalchemy import select

            users = (await db.execute(select(models.User))).scalars().all()
            user = users[0] if users else None

        if user and user.telegram_chat_id:
            import uuid

            approval = models.Approval(
                id=uuid.uuid4(),
                user_id=user.id,
                agent=self.name,
                action_type="telegram.send_briefing",
                rendered_text=formatted,
                status="pending",
            )
            async with session() as db:
                db.add(approval)

            await self.publish(
                "approval.requested",
                {
                    "approval_id": str(approval.id),
                    "agent": self.name,
                    "action_type": "telegram.send_briefing",
                    "rendered_text": formatted,
                },
            )

    async def _notify_plan_ready(self, payload: dict[str, Any]) -> None:
        """Notify user that a new study plan is ready."""
        await self.log_event(
            type="secretary.notify_plan.start",
            payload={},
        )

        text = (
            f"Your study plan has been updated!\n"
            f"Blocks: {payload.get('block_count', 0)}\n"
            f"Summary: {payload.get('summary', 'N/A')}"
        )

        await self.publish(
            "notification.plan_ready",
            {"text": text},
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(Secretary().run())
