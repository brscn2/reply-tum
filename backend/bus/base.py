"""EventBus interface — all agents import only from here."""

from __future__ import annotations

import abc
import os
import uuid
from typing import Any

from backend.db import models
from backend.db.session import session


class EventBus(abc.ABC):
    @abc.abstractmethod
    async def publish(self, event_type: str, payload: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    async def subscribe(self, event_types: list[str]) -> None: ...

    @abc.abstractmethod
    async def next_event(self) -> dict[str, Any]: ...


class InProcessBus(EventBus):
    """Simple in-process bus using asyncio.Queue — good for local dev and demo."""

    _instance: InProcessBus | None = None
    _queues: dict[str, list[Any]]

    def __init__(self) -> None:
        import asyncio

        self._queues = {}
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @classmethod
    def get(cls) -> InProcessBus:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "id": str(uuid.uuid4()),
            "type": event_type,
            "payload": payload,
        }
        for inbox in self._queues.get(event_type, []):
            await inbox.put(event)

    async def subscribe(self, event_types: list[str]) -> None:
        for et in event_types:
            self._queues.setdefault(et, []).append(self._inbox)

    async def next_event(self) -> dict[str, Any]:
        return await self._inbox.get()


async def log_event(
    agent_name: str, event_type: str, payload: dict[str, Any]
) -> models.AgentEvent:
    row = models.AgentEvent(
        id=uuid.uuid4(),
        type=event_type,
        agent=agent_name,
        payload=payload,
    )
    async with session() as db:
        db.add(row)
    return row


def get_bus() -> EventBus:
    driver = os.getenv("EVENT_BUS_DRIVER", "in_process")
    if driver == "sqs":
        from backend.bus.sqs import SQSBus

        return SQSBus()
    if driver == "pg_notify":
        from backend.bus.pg_notify import PGNotifyBus

        return PGNotifyBus()
    return InProcessBus.get()
