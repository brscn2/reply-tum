"""Llama 4 Bedrock client for cheap triage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import boto3
import structlog

from backend.bedrock.config import BEDROCK_REGION, LLAMA

log = structlog.get_logger()

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ".llm_cache"))
CACHE_DIR.mkdir(exist_ok=True)

_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _client


def _cache_key(prompt: str) -> str:
    blob = json.dumps({"model": LLAMA, "prompt": prompt}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


async def triage(prompt: str) -> bool:
    key = _cache_key(prompt)
    cache_path = CACHE_DIR / f"{key}.json"
    if cache_path.exists():
        log.info("cache_hit", model="llama")
        return json.loads(cache_path.read_text())["result"]

    client = _get_client()
    body = json.dumps(
        {
            "prompt": prompt,
            "max_gen_len": 10,
            "temperature": 0.1,
        }
    )

    response = client.invoke_model(modelId=LLAMA, body=body)
    result = json.loads(response["body"].read())
    text = result.get("generation", "").strip().lower()
    answer = text.startswith("yes")

    cache_path.write_text(json.dumps({"result": answer}))
    return answer
