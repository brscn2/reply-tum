"""Titan Embed v2 Bedrock client."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import boto3
import structlog

from backend.bedrock.config import BEDROCK_REGION, TITAN_EMBED

log = structlog.get_logger()

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ".llm_cache"))
CACHE_DIR.mkdir(exist_ok=True)

_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _client


def _cache_key(text: str) -> str:
    blob = json.dumps({"model": TITAN_EMBED, "text": text}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


async def embed(text: str) -> list[float]:
    key = _cache_key(text)
    cache_path = CACHE_DIR / f"{key}.json"
    if cache_path.exists():
        log.info("cache_hit", model="titan")
        return json.loads(cache_path.read_text())["embedding"]

    client = _get_client()
    body = json.dumps(
        {
            "inputText": text,
        }
    )

    response = client.invoke_model(modelId=TITAN_EMBED, body=body)
    result = json.loads(response["body"].read())
    embedding = result["embedding"]

    cache_path.write_text(json.dumps({"embedding": embedding}))
    return embedding
