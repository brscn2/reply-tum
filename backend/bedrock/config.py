"""Bedrock model ID constants — change here, nowhere else."""

import os

CLAUDE_OPUS = os.getenv("BEDROCK_CLAUDE_OPUS", "anthropic.claude-opus-4-6-v1")
CLAUDE_SONNET = os.getenv("BEDROCK_CLAUDE_SONNET", "anthropic.claude-sonnet-4-6-v1")
NOVA_PRO = os.getenv("BEDROCK_NOVA_PRO", "amazon.nova-pro-v1")
TITAN_EMBED = os.getenv("BEDROCK_TITAN_EMBED", "amazon.titan-embed-text-v2")
LLAMA = os.getenv("BEDROCK_LLAMA", "meta.llama-4-scout-v1")
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "eu-central-1")
