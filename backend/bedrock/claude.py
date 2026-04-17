"""Claude Opus + Sonnet Bedrock clients."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import boto3
import structlog

from backend.bedrock.config import BEDROCK_REGION, CLAUDE_OPUS, CLAUDE_SONNET

log = structlog.get_logger()

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ".llm_cache"))
CACHE_DIR.mkdir(exist_ok=True)

_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _client


def _cache_key(model: str, system: str, prompt: str) -> str:
    blob = json.dumps({"model": model, "system": system, "prompt": prompt}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _read_cache(key: str) -> str | None:
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())["response"]
    return None


def _write_cache(key: str, response: str) -> None:
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps({"response": response}))


async def _invoke(model_id: str, system: str, prompt: str) -> str:
    key = _cache_key(model_id, system, prompt)
    cached = _read_cache(key)
    if cached is not None:
        log.info("cache_hit", model=model_id)
        return cached

    client = _get_client()
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    response = client.invoke_model(modelId=model_id, body=body)
    result = json.loads(response["body"].read())
    text = result["content"][0]["text"]

    _write_cache(key, text)
    return text


async def opus(system: str = "", prompt: str = "") -> str:
    return await _invoke(CLAUDE_OPUS, system, prompt)


async def sonnet(system: str = "", prompt: str = "") -> str:
    return await _invoke(CLAUDE_SONNET, system, prompt)
