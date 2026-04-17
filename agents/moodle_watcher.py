"""Moodle Watcher agent — monitors course uploads."""

from __future__ import annotations

import os
from typing import Any

import structlog

from agents.base import Agent
from backend.db import models
from backend.db.session import session

log = structlog.get_logger()

MOCK_MODE = os.getenv("SCHATTEN_INTEGRATION_MODE", "mock") == "mock"


class MoodleWatcher(Agent):
    name = "moodle_watcher"
    subscribes_to = ["schedule.poll.moodle"]
    poll_interval_seconds = 300

    async def handle(self, event: dict[str, Any]) -> None:
        await self.log_event(
            type="moodle_watcher.handle.start",
            payload={"trigger": event["type"]},
        )
        await self._check_all_courses()

    async def poll(self) -> None:
        await self.log_event(
            type="moodle_watcher.poll.start",
            payload={},
        )
        await self._check_all_courses()

    async def _check_all_courses(self) -> None:
        async with session() as db:
            from sqlalchemy import select

            courses = (await db.execute(select(models.Course))).scalars().all()

        for course in courses:
            new_uploads = await self._fetch_uploads(course)
            for upload in new_uploads:
                triage_worthy = await self._triage(upload)
                if not triage_worthy:
                    continue

                summary, concepts = await self._summarize(upload)

                async with session() as db:
                    row = models.Upload(
                        course_id=course.id,
                        filename=upload["filename"],
                        s3_key=upload.get("s3_key"),
                        summary=summary,
                        concepts=concepts,
                    )
                    db.add(row)

                await self.publish(
                    "course.upload.new",
                    {
                        "course_id": str(course.id),
                        "course_name": course.name,
                        "filename": upload["filename"],
                        "summary": summary,
                        "concepts": concepts,
                    },
                )

    async def _fetch_uploads(self, course: models.Course) -> list[dict[str, Any]]:
        if MOCK_MODE:
            from integrations.moodle_mock import get_uploads

            return await get_uploads(course.moodle_id)
        else:
            from integrations.moodle_playwright import get_uploads

            return await get_uploads(course.moodle_id)

    async def _triage(self, upload: dict[str, Any]) -> bool:
        """Use Llama for cheap triage — is this upload worth processing?"""
        await self.log_event(
            type="moodle_watcher.triage.start",
            payload={"filename": upload["filename"]},
        )

        from backend.bedrock.llama import triage

        return await triage(
            prompt=(
                f"Is this course upload worth summarizing for a student? "
                f"Filename: {upload['filename']}. "
                f"Respond with just 'yes' or 'no'."
            )
        )

    async def _summarize(self, upload: dict[str, Any]) -> tuple[str, list[str]]:
        """Use Sonnet for slide summary + concept extraction."""
        await self.log_event(
            type="moodle_watcher.summarize.start",
            payload={"filename": upload["filename"]},
        )

        from backend.bedrock.claude import sonnet

        result = await sonnet(
            system="You summarize course materials for a TUM student.",
            prompt=(
                f"Summarize this upload and extract key concepts.\n\n"
                f"Filename: {upload['filename']}\n"
                f"Content: {upload.get('content', 'N/A')}\n\n"
                f"Respond as JSON: {{\"summary\": \"...\", \"concepts\": [\"...\"]}}"
            ),
        )

        import json

        try:
            parsed = json.loads(result)
            return parsed["summary"], parsed["concepts"]
        except (json.JSONDecodeError, KeyError):
            return result, []


if __name__ == "__main__":
    import asyncio

    asyncio.run(MoodleWatcher().run())
