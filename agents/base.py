"""Agent base class — all agents inherit from this."""

from __future__ import annotations

import abc
import asyncio
from typing import Any

import structlog

from backend.bus.base import EventBus, get_bus, log_event

logger = structlog.get_logger()


class Agent(abc.ABC):
    name: str = "unnamed"
    subscribes_to: list[str] = []
    poll_interval_seconds: float = 0

    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or get_bus()
        self.log = logger.bind(agent=self.name)

    async def log_event(self, type: str, payload: dict[str, Any]) -> None:
        """Write to events table FIRST (rule 5.3), then return."""
        await log_event(self.name, type, payload)
        self.log.info("event_logged", event_type=type)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        await log_event(self.name, event_type, payload)
        await self.bus.publish(event_type, payload)
        self.log.info("event_published", event_type=event_type)

    @abc.abstractmethod
    async def handle(self, event: dict[str, Any]) -> None:
        """Process one inbound event. Subclasses must implement."""

    async def poll(self) -> None:
        """Override for agents that need periodic polling (e.g. Moodle scraping)."""

    async def run(self) -> None:
        self.log.info("starting", subscribes_to=self.subscribes_to)
        await self.bus.subscribe(self.subscribes_to)

        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self._event_loop()),
        ]
        if self.poll_interval_seconds > 0:
            tasks.append(asyncio.create_task(self._poll_loop()))

        await asyncio.gather(*tasks)

    async def _event_loop(self) -> None:
        while True:
            event = await self.bus.next_event()
            try:
                await self.handle(event)
            except Exception:
                self.log.exception("handle_error", event_type=event.get("type"))

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                self.log.exception("poll_error")
            await asyncio.sleep(self.poll_interval_seconds)
