"""Room Scout agent — STRETCH goal."""

from __future__ import annotations

from typing import Any

import structlog

from agents.base import Agent

log = structlog.get_logger()


class RoomScout(Agent):
    name = "room_scout"
    subscribes_to = [
        "plan.generated",
        "deadline.risk.escalated",
    ]

    async def handle(self, event: dict[str, Any]) -> None:
        await self.log_event(
            type="room_scout.handle.start",
            payload={"trigger": event["type"]},
        )

        if event["type"] == "plan.generated":
            await self._suggest_rooms_for_plan(event["payload"])
        elif event["type"] == "deadline.risk.escalated":
            await self._suggest_room_now(event["payload"])

    async def _suggest_rooms_for_plan(self, payload: dict[str, Any]) -> None:
        """Find available study rooms for planned study blocks."""
        await self.log_event(
            type="room_scout.search.start",
            payload={"block_count": payload.get("block_count", 0)},
        )

        # TODO: integrate with TUM room availability API
        suggestions = [
            {
                "room": "Bibliothek Stammgelände",
                "available": True,
                "distance_minutes": 5,
            },
            {
                "room": "MW Lernraum 0001",
                "available": True,
                "distance_minutes": 8,
            },
        ]

        await self.publish(
            "room.suggestions.ready",
            {
                "plan_summary": payload.get("summary", ""),
                "suggestions": suggestions,
            },
        )

    async def _suggest_room_now(self, payload: dict[str, Any]) -> None:
        """Urgent deadline — find a room available right now."""
        await self.log_event(
            type="room_scout.urgent_search.start",
            payload={"deadline_id": payload.get("deadline_id")},
        )

        await self.publish(
            "room.suggestion.urgent",
            {
                "deadline_title": payload.get("title", ""),
                "room": "Bibliothek Stammgelände",
                "available": True,
            },
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(RoomScout().run())
