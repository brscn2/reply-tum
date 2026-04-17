"""Social Scout agent — watches TUMi + Luma for social events."""

from __future__ import annotations

import os
from typing import Any

import structlog

from agents.base import Agent
from backend.db import models
from backend.db.session import session

log = structlog.get_logger()

MOCK_MODE = os.getenv("SCHATTEN_INTEGRATION_MODE", "mock") == "mock"


class SocialScout(Agent):
    name = "social_scout"
    subscribes_to = ["schedule.poll.social"]
    poll_interval_seconds = 1800

    async def handle(self, event: dict[str, Any]) -> None:
        await self.log_event(
            type="social_scout.handle.start",
            payload={"trigger": event["type"]},
        )
        await self._scan_events()

    async def poll(self) -> None:
        await self.log_event(
            type="social_scout.poll.start",
            payload={},
        )
        await self._scan_events()

    async def _scan_events(self) -> None:
        tumi_events = await self._fetch_tumi()
        luma_events = await self._fetch_luma()
        all_events = tumi_events + luma_events

        await self.log_event(
            type="social_scout.fetched",
            payload={
                "tumi_count": len(tumi_events),
                "luma_count": len(luma_events),
            },
        )

        user_embedding = await self._get_user_embedding()
        ranked = await self._rank_events(all_events, user_embedding)

        for event_data in ranked:
            await self._store_event(event_data)

        if ranked:
            await self.publish(
                "social.events.ranked",
                {
                    "count": len(ranked),
                    "top": ranked[:5],
                },
            )

    async def _fetch_tumi(self) -> list[dict[str, Any]]:
        if MOCK_MODE:
            from integrations.tumi_mock import get_events

            return await get_events()
        else:
            from integrations.tumi_scraper import get_events

            return await get_events()

    async def _fetch_luma(self) -> list[dict[str, Any]]:
        if MOCK_MODE:
            from integrations.luma_mock import get_events

            return await get_events()
        else:
            from integrations.luma_scraper import get_events

            return await get_events()

    async def _get_user_embedding(self) -> list[float]:
        async with session() as db:
            from sqlalchemy import select

            users = (await db.execute(select(models.User))).scalars().all()
            user = users[0] if users else None

        if not user:
            return []

        from backend.bedrock.titan import embed

        prefs = user.preferences or {}
        text = f"Interests: {prefs.get('interests', 'socializing, technology, culture')}"
        return await embed(text)

    async def _rank_events(
        self,
        events: list[dict[str, Any]],
        user_embedding: list[float],
    ) -> list[dict[str, Any]]:
        if not events:
            return []

        await self.log_event(
            type="social_scout.rank.start",
            payload={"count": len(events)},
        )

        from backend.bedrock.titan import embed

        for event in events:
            text = f"{event['title']} — {event.get('description', '')}"
            event["embedding"] = await embed(text)

        if user_embedding:
            for event in events:
                event["relevance_score"] = self._cosine_similarity(
                    user_embedding, event["embedding"]
                )
            events.sort(key=lambda e: e.get("relevance_score", 0), reverse=True)

        top_events = events[:10]
        await self._add_explanations(top_events)
        return top_events

    async def _add_explanations(self, events: list[dict[str, Any]]) -> None:
        from backend.bedrock.claude import sonnet

        for event in events:
            await self.log_event(
                type="social_scout.explain.start",
                payload={"title": event["title"]},
            )

            event["explanation"] = await sonnet(
                system="You explain why a social event is relevant to a TUM student.",
                prompt=(
                    f"Event: {event['title']}\n"
                    f"Description: {event.get('description', 'N/A')}\n"
                    f"Relevance score: {event.get('relevance_score', 'N/A')}\n\n"
                    f"Write ONE sentence explaining why this event is worth attending."
                ),
            )

    async def _store_event(self, event_data: dict[str, Any]) -> None:
        async with session() as db:
            from datetime import datetime, timezone

            row = models.SocialEvent(
                source=event_data.get("source", "unknown"),
                title=event_data["title"],
                description=event_data.get("description"),
                url=event_data.get("url"),
                starts_at=event_data.get(
                    "starts_at", datetime.now(timezone.utc)
                ),
                location=event_data.get("location"),
                embedding=event_data.get("embedding"),
                relevance_score=event_data.get("relevance_score"),
                relevance_explanation=event_data.get("explanation"),
            )
            db.add(row)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


if __name__ == "__main__":
    import asyncio

    asyncio.run(SocialScout().run())
