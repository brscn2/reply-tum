"""Nova Pro Bedrock client."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import boto3
import structlog

from backend.bedrock.config import BEDROCK_REGION, NOVA_PRO

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
    blob = json.dumps({"model": NOVA_PRO, "prompt": prompt}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


async def generate(prompt: str) -> str:
    key = _cache_key(prompt)
    cache_path = CACHE_DIR / f"{key}.json"
    if cache_path.exists():
        log.info("cache_hit", model="nova")
        return json.loads(cache_path.read_text())["response"]

    client = _get_client()
    body = json.dumps(
        {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 1024},
        }
    )

    response = client.invoke_model(modelId=NOVA_PRO, body=body)
    result = json.loads(response["body"].read())
    text = result["output"]["message"]["content"][0]["text"]

    cache_path.write_text(json.dumps({"response": text}))
    return text
