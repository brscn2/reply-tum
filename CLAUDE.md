# CLAUDE.md — Schatten

> This file is the orientation doc Claude Code reads on every session. It's the **operating manual**. The full design reference is `SCHATTEN_SPEC.md` — when in doubt, that spec wins.

---

## 1. What this is

**Schatten** is a proactive multi-agent daemon that runs a TUM student's life in the background — watching Moodle, TUMonline, Google Calendar, ESN TUMi, and Luma Munich; deciding; drafting actions; asking for approval via Telegram.

Five agents, one orchestrator, an event bus, a knowledge graph, and a Mission Control dashboard. Not a chatbot.

**This is a 24-hour hackathon build.** Every decision is biased toward "demos well" over "scales well." We'll optimize later if the judges let us.

**Non-negotiable thesis:** agents are *processes*, not function calls. A multi-agent system is a multi-process system. If Claude Code ever wants to "just have the orchestrator call the sub-agent as a function" — stop, re-read §3 of the spec.

---

## 2. Quick start

```bash
# one-time setup
cp .env.example .env               # fill in AWS + Google + Telegram secrets
docker compose up -d postgres      # wait for health
uv sync                            # or poetry install
cd frontend && pnpm install && cd ..

# seed demo data
python -m infra.seed

# run everything
docker compose up                  # brings up all agents + frontend + postgres

# run one agent in foreground (for debugging)
python -m agents.moodle_watcher
```

Frontend: `http://localhost:3000`. Mission Control is the home page.
Backend API: `http://localhost:8000`. Event stream at `/api/events/stream`.

---

## 3. Folder structure

```
schatten/
├── CLAUDE.md                      # this file
├── SCHATTEN_SPEC.md               # full spec — source of truth
├── README.md                      # user-facing intro
├── docker-compose.yml
├── .env.example
├── pyproject.toml                 # ruff + black + mypy config
│
├── backend/
│   ├── api/
│   │   ├── events.py              # SSE /events/stream
│   │   ├── plans.py               # GET /api/plans/latest
│   │   └── telegram.py            # POST /api/telegram/webhook
│   ├── bus/
│   │   ├── base.py                # EventBus interface — DO NOT bypass
│   │   ├── sqs.py                 # AWS SQS implementation
│   │   └── pg_notify.py           # Postgres LISTEN/NOTIFY fallback
│   ├── db/
│   │   ├── models.py              # SQLAlchemy models
│   │   ├── migrations/            # Alembic
│   │   └── session.py
│   └── bedrock/
│       ├── config.py              # model ID constants — change here, nowhere else
│       ├── claude.py              # Opus + Sonnet clients
│       ├── nova.py
│       ├── titan.py               # embeddings
│       └── llama.py               # cheap triage
│
├── agents/
│   ├── base.py                    # Agent base class — all agents inherit
│   ├── moodle_watcher.py
│   ├── deadline_sentinel.py
│   ├── calendar_sync.py
│   ├── social_scout.py
│   ├── study_planner.py           # orchestrator — build last
│   ├── room_scout.py              # STRETCH
│   └── secretary.py               # STRETCH
│
├── models/
│   └── miss_probability.py        # logistic heuristic + LLM rationale
│
├── integrations/
│   ├── moodle_playwright.py       # real Moodle scraper
│   ├── moodle_mock.py             # local FastAPI mock — use this for demo
│   ├── tumonline_ical.py
│   ├── tumi_scraper.py
│   ├── tumi_mock.py
│   ├── luma_scraper.py
│   ├── luma_mock.py
│   ├── gcal_client.py             # Google Calendar OAuth client
│   ├── cognee_client.py           # knowledge graph
│   └── telegram_bot.py
│
├── frontend/                      # Next.js 14 Mission Control
│   ├── app/
│   │   ├── page.tsx               # agent graph + event feed
│   │   ├── plan/page.tsx
│   │   └── concepts/page.tsx
│   ├── components/
│   │   ├── AgentGraph.tsx         # React Flow
│   │   ├── EventFeed.tsx
│   │   ├── MissProbBadge.tsx
│   │   └── PlanTimeline.tsx
│   └── lib/
│       └── sse.ts
│
├── infra/
│   ├── seed.py                    # seed demo user + courses + slides
│   └── fixtures/                  # sample PDFs, iCal, JSON for mocks
│
└── tests/
    ├── agents/
    └── integrations/
```

---

## 4. Tech stack — quick reference

| Layer | Choice |
|---|---|
| Backend | FastAPI, Python 3.11, SQLAlchemy 2.x, Alembic |
| Event bus | SQS (default); `pg_notify` fallback behind same interface |
| Agent workers | One Python process per agent, managed by `docker compose` |
| Web automation | Playwright (headless Chromium) |
| LLM | AWS Bedrock — Claude Opus 4.6 / Sonnet, Nova Pro, Titan Embed v2, Llama 4 |
| Knowledge graph | cognee |
| Vector store | cognee built-in + pgvector (for social event matching) |
| Object store | S3 |
| Database | Postgres 16 |
| Calendar | Google Calendar API (OAuth 2.0, refresh tokens) |
| Frontend | Next.js 14 App Router, Tailwind, shadcn/ui, React Flow |
| Live updates | Server-Sent Events |
| Approvals | Telegram bot via `python-telegram-bot` |

---

## 5. Hard rules — do not break these

These are the rules that, if violated, silently break the demo magic. Claude Code must follow them without asking.

### 5.1 Agents are processes, not functions
- Each agent runs in its own process and subscribes to events via `EventBus`.
- **Never** have one agent directly `import` another and call a method on it.
- Cross-agent communication is **always** via events.

### 5.2 Every mutating action is approval-gated
- If an action writes to Moodle, Google Calendar, TUMi, Luma, or sends email — it goes through `approval.requested` first.
- The Telegram bot is the only approval UI. Mission Control shows the request but does not accept approval.
- **Never** have an agent call an external mutating API directly without a granted approval in the `approvals` table.

### 5.3 Every agent logs to `events` before calling an LLM
- Insert the row, commit, then call Bedrock.
- This is what makes Mission Control show the attempt even when Bedrock fails or lags.
- If Claude Code is tempted to "log after LLM returns" — stop. Log first.

### 5.4 Bedrock model IDs live in one file
- All model ID constants: `backend/bedrock/config.py`
- **Never** hardcode a model ID in agent code. Import from config.
- Reasoning: demo-day Bedrock quota may force swaps. One file = one minute fix.

### 5.5 Mock-first integrations
- For every external service (Moodle, TUMonline, TUMi, Luma, Google Calendar), there is a **mock twin** with identical input/output shape.
- `.env` flag `SCHATTEN_INTEGRATION_MODE=mock|live` switches all of them.
- **Demo always runs in mock mode.** Live mode is for post-hackathon.
- Never demo against a live external system. It will fail at the worst possible moment.

### 5.6 All LLM calls are cached by content hash
- `backend/bedrock/` wraps every client with a content-hash cache (SHA256 of prompt + model + params → response).
- Dev cache lives in `.llm_cache/`, committed to `.gitignore`.
- Before the demo, pre-run the full flow 3× to warm the cache.

### 5.7 The EventBus abstraction is sacred
- `backend/bus/base.py` defines the interface. Agents import **only** from there.
- If SQS breaks during the hackathon, we swap to `pg_notify` with one env var. This only works if no agent code knows which is active.

### 5.8 Never commit secrets
- `.env` is gitignored. `.env.example` is committed with placeholder values.
- If you see a real token in a diff, stop and rotate it.

---

## 6. How to add a new agent (the template)

Every agent follows this pattern. Copy an existing one — don't invent a new shape.

```python
# agents/new_agent.py
from agents.base import Agent
from backend.bedrock import claude  # or nova, titan, llama
from backend.db import session, models
import structlog

log = structlog.get_logger()

class NewAgent(Agent):
    name = "new_agent"
    subscribes_to = ["event.type.one", "event.type.two"]

    async def handle(self, event: dict) -> None:
        # 1. Log the attempt FIRST (rule 5.3)
        await self.log_event(
            type="new_agent.handle.start",
            payload={"trigger": event["type"]},
        )

        # 2. Pull state from Postgres
        async with session() as db:
            user = await db.get(models.User, event["user_id"])

        # 3. Call LLM (cached) or compute
        result = await claude.sonnet(prompt=..., system=...)

        # 4. Decide: mutating action? -> approval.requested
        #           non-mutating? -> publish result event
        if result.mutating:
            await self.publish("approval.requested", {
                "action_id": ...,
                "rendered_text": result.rendered,
            })
        else:
            await self.publish("new_agent.result", {...})

if __name__ == "__main__":
    import asyncio
    asyncio.run(NewAgent().run())
```

Then register the agent in `docker-compose.yml` as its own service.

---

## 7. LLM routing — which model for what

Follow this table. If you want to use a different model, justify it in the commit message.

| Task | Model | Reason |
|---|---|---|
| Triage (is this upload/event worth processing?) | Llama 4 / DeepSeek R1 | cheap, fast |
| Slide summary | Claude Sonnet | quality matters, speed matters more than max reasoning |
| Miss-probability rationale | Claude Sonnet | one sentence, no deep reasoning |
| Social event rerank + explanation | Claude Sonnet | |
| Telegram approval text | Claude Sonnet | tone matters |
| **Study Planner — the plan itself** | **Claude Opus 4.6** | hard multi-constraint reasoning; the one place Opus earns its cost |
| Slide embeddings | Titan Embed v2 | |
| Event embeddings | Titan Embed v2 | |
| User profile embedding | Titan Embed v2 | |
| Morning briefing (text) | Nova Pro | |
| Morning briefing (voice, stretch) | Nova Pro TTS | |

---

## 8. Event bus contract

### Publishing
```python
await self.publish("course.upload.new", {
    "course_id": "...",
    "upload_id": "...",
    "summary": "...",
    "concepts": [...],
})
```
Every publish also writes a row to the `events` table — Mission Control tails that table.

### Subscribing
Declared as class attribute `subscribes_to: list[str]`. The `Agent` base class wires up SQS subscriptions (or `pg_notify` channels) automatically.

### Event type naming
- Format: `<domain>.<noun>.<verb>` — e.g. `course.upload.new`, `deadline.risk.escalated`
- See §7 of the spec for the full taxonomy. If you need a new event type, add it there first.

---

## 9. Commands cheat sheet

```bash
# database
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1

# run a single agent in foreground (debugging)
python -m agents.moodle_watcher
python -m agents.study_planner

# test a single integration
python -m integrations.moodle_playwright --course COURSE_ID

# run the mock Moodle server standalone
uvicorn integrations.moodle_mock:app --port 9001

# seed / reset demo data
python -m infra.seed --reset

# frontend dev
cd frontend && pnpm dev

# typecheck + lint
ruff check .
black --check .
mypy agents/ backend/
cd frontend && pnpm typecheck && pnpm lint

# run tests
pytest tests/ -x -v
```

---

## 10. Environment variables

Set in `.env` (gitignored). Template is `.env.example`.

```bash
# mode
SCHATTEN_INTEGRATION_MODE=mock        # mock | live

# database
DATABASE_URL=postgresql+asyncpg://schatten:schatten@postgres:5432/schatten

# AWS
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=eu-central-1
BEDROCK_REGION=eu-central-1
S3_BUCKET=schatten-demo

# Bedrock model IDs (override defaults in bedrock/config.py)
BEDROCK_CLAUDE_OPUS=anthropic.claude-opus-4-6-...
BEDROCK_CLAUDE_SONNET=anthropic.claude-sonnet-...
BEDROCK_NOVA_PRO=amazon.nova-pro-...
BEDROCK_TITAN_EMBED=amazon.titan-embed-text-v2...
BEDROCK_LLAMA=meta.llama-4-...

# event bus
EVENT_BUS_DRIVER=sqs                  # sqs | pg_notify
SQS_QUEUE_URL=https://sqs.eu-central-1.amazonaws.com/.../schatten-events

# telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_URL=https://....ngrok.app/api/telegram/webhook

# google
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/google/callback

# TUM (demo uses mock, live mode would fill these)
TUM_USERNAME=...
TUM_PASSWORD=...

# cognee
COGNEE_API_KEY=...
```

---

## 11. Code style

### Python
- `ruff` + `black` for formatting. Line length 100.
- `mypy --strict` on `agents/`, `backend/`, `models/`. Integrations can be looser.
- Type hints everywhere.
- `structlog` for logging, never `print`.
- Prefer `async` / `await` for IO. All agent handlers are async.
- Pydantic v2 for event payloads and LLM structured outputs.

### TypeScript / Frontend
- Strict mode on.
- `shadcn/ui` components preferred over hand-rolled.
- Tailwind only. No CSS modules, no styled-components.
- No `localStorage` (we're not an artifact, but still — state lives server-side).

### Commits
- Conventional-ish: `feat(moodle): ...`, `fix(planner): ...`, `chore: ...`.
- One concern per commit.

---

## 12. When Claude Code is stuck

Check in this order:
1. **`SCHATTEN_SPEC.md`** — full design reference. Search by section number.
2. **This file §5** — hard rules. If tempted to break one, read the reasoning again.
3. **Existing agent code** — every agent follows the §6 template. Copy the closest match.
4. **Ask the human** — don't guess on secrets, auth flows, or model quotas.

---

## 13. Anti-patterns Claude Code will be tempted by (and must resist)

| Tempting | Instead |
|---|---|
| "Let me have the Study Planner directly call `moodle_watcher.fetch()`" | Publish an event. Agents never import each other. |
| "I'll just call Bedrock with the model string `anthropic.claude-...`" | Import from `backend/bedrock/config.py`. |
| "Let me book the Google Calendar event directly, it's obviously fine" | `approval.requested` first. Always. |
| "I'll demo against the real Moodle to prove it works" | No. Use `moodle_mock`. The real thing will fail on stage. |
| "Let's use LangChain / LlamaIndex / CrewAI, it has agent abstractions" | No. Plain Python + EventBus. Every abstraction we add is a thing that can fail at 3am. |
| "The logistic coefficients look hacky, let me train a real model" | The hacky version is the feature. It's *defensible* precisely because it's simple. |
| "Let me add voice before the text briefing works" | Text first. Voice is stretch. Read §2 of the spec. |

---

## 14. Status tracking

Current state (update as you go):

- [ ] Hours 0–3: skeleton
- [ ] Hours 3–10: five agents publishing to bus
- [ ] Hours 10–14: Study Planner orchestration + cognee
- [ ] Hours 14–19: Mission Control UI
- [ ] Hours 19–22: integration rehearsal + cache warmup
- [ ] Hours 22–24: pitch + Q&A prep

Demo-blocker open issues: *(add here as they come up)*
