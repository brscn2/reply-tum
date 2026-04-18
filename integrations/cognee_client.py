"""Cognee knowledge graph client.

Extracts downloaded Moodle course zips, feeds PDFs into cognee
as per-course datasets, builds a knowledge graph, and provides
search/recall over course materials.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

DOWNLOAD_DIR = Path(os.getenv("SCHATTEN_DOWNLOAD_DIR", "downloads"))
EXTRACT_DIR = Path(os.getenv("SCHATTEN_EXTRACT_DIR", "extracted"))


def _safe_dataset_name(name: str) -> str:
    keep = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    raw = name.lower().replace(" ", "_").replace("-", "_")
    return "".join(c if c in keep else "" for c in raw)[:60] or "course"


# ---------------------------------------------------------------------------
# Step 1: Extract zips into per-course folders
# ---------------------------------------------------------------------------

def extract_all_zips(download_dir: Path | None = None) -> list[dict[str, Any]]:
    """Unzip all course zips into EXTRACT_DIR. Returns metadata per course."""
    dl = download_dir or DOWNLOAD_DIR
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for zip_path in sorted(dl.rglob("*.zip")):
        course_name = zip_path.parent.name
        dest = EXTRACT_DIR / _safe_dataset_name(course_name)

        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest)

        files = list(dest.rglob("*"))
        pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
        all_files = [f for f in files if f.is_file()]

        log.info(
            "cognee.extract.done",
            course=course_name,
            total_files=len(all_files),
            pdfs=len(pdfs),
        )
        results.append({
            "course_name": course_name,
            "dataset_name": _safe_dataset_name(course_name),
            "extract_path": str(dest),
            "total_files": len(all_files),
            "pdf_count": len(pdfs),
            "file_paths": [str(f) for f in all_files],
            "pdf_paths": [str(f) for f in pdfs],
        })

    return results


# ---------------------------------------------------------------------------
# Step 2: Configure cognee for available LLM provider
# ---------------------------------------------------------------------------

def _configure_cognee() -> None:
    """Set up cognee's LLM and embedding config from environment."""
    from cognee.infrastructure.llm.config import LLMConfig

    llm_config = LLMConfig()

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if anthropic_key:
        llm_config.llm_provider = "anthropic"
        llm_config.llm_model = "anthropic/claude-sonnet-4-20250514"
        llm_config.llm_api_key = anthropic_key
        log.info("cognee.config.provider", provider="anthropic")
    elif openai_key:
        llm_config.llm_provider = "openai"
        llm_config.llm_model = "openai/gpt-4o-mini"
        llm_config.llm_api_key = openai_key
        log.info("cognee.config.provider", provider="openai")
    else:
        log.warning("cognee.config.no_llm_key", msg="Set ANTHROPIC_API_KEY or OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# Step 3: Ingest into cognee — one dataset per course
# ---------------------------------------------------------------------------

async def ingest_course(
    dataset_name: str,
    file_paths: list[str],
) -> dict[str, Any]:
    """Add files to cognee and build the knowledge graph for one course."""
    import cognee

    _configure_cognee()

    pdf_paths = [p for p in file_paths if p.lower().endswith(".pdf")]
    if not pdf_paths:
        log.info("cognee.ingest.skip", dataset=dataset_name, reason="no PDFs")
        return {"dataset": dataset_name, "status": "skipped", "reason": "no PDFs"}

    log.info("cognee.ingest.start", dataset=dataset_name, files=len(pdf_paths))

    await cognee.add(
        data=[open(p, "rb") for p in pdf_paths],
        dataset_name=dataset_name,
    )
    log.info("cognee.ingest.added", dataset=dataset_name)

    await cognee.cognify(datasets=[dataset_name])
    log.info("cognee.ingest.cognified", dataset=dataset_name)

    return {"dataset": dataset_name, "status": "ok", "files_ingested": len(pdf_paths)}


async def ingest_all_courses(
    courses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract zips and ingest all courses into cognee."""
    if courses is None:
        courses = extract_all_zips()

    results: list[dict[str, Any]] = []
    for course in courses:
        try:
            result = await ingest_course(
                dataset_name=course["dataset_name"],
                file_paths=course["pdf_paths"],
            )
            results.append(result)
        except Exception as exc:
            log.exception("cognee.ingest.error", dataset=course["dataset_name"])
            results.append({
                "dataset": course["dataset_name"],
                "status": f"error: {exc}",
            })

    return results


# ---------------------------------------------------------------------------
# Step 4: Search / recall over the knowledge graph
# ---------------------------------------------------------------------------

async def search_course(
    query: str,
    dataset_name: str | None = None,
    top_k: int = 10,
) -> list[Any]:
    """Search the knowledge graph for a query, optionally scoped to a course."""
    import cognee
    from cognee.modules.search.types.SearchType import SearchType

    _configure_cognee()

    kwargs: dict[str, Any] = {
        "query_text": query,
        "query_type": SearchType.GRAPH_COMPLETION,
        "top_k": top_k,
    }
    if dataset_name:
        kwargs["datasets"] = [dataset_name]

    results = await cognee.search(**kwargs)
    log.info("cognee.search.done", query=query[:50], results=len(results))
    return results


async def get_course_graph(dataset_name: str) -> dict[str, Any]:
    """Retrieve the knowledge graph structure for a course.

    Returns nodes and edges suitable for the frontend roadmap visualization.
    """
    import cognee
    from cognee.modules.search.types.SearchType import SearchType

    _configure_cognee()

    results = await cognee.search(
        query_text=f"List all concepts, topics, and their relationships in this course",
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[dataset_name],
        top_k=50,
    )

    return {
        "dataset": dataset_name,
        "graph_results": [
            {
                "content": str(r.content) if hasattr(r, "content") else str(r),
                "score": getattr(r, "score", None),
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    import sys

    if "--extract-only" in sys.argv:
        courses = extract_all_zips()
        print(f"\nExtracted {len(courses)} courses:\n")
        for c in courses:
            print(f"  {c['dataset_name']}: {c['pdf_count']} PDFs, {c['total_files']} total files")
            print(f"    -> {c['extract_path']}")
        return

    print("Step 1: Extracting zips...\n")
    courses = extract_all_zips()
    for c in courses:
        print(f"  {c['dataset_name']}: {c['pdf_count']} PDFs")

    if "--ingest" in sys.argv:
        print("\nStep 2: Ingesting into cognee...\n")
        results = await ingest_all_courses(courses)
        print("\n--- Ingestion Results ---\n")
        for r in results:
            print(f"  [{r['status']}] {r['dataset']}")

    if "--search" in sys.argv:
        query_idx = sys.argv.index("--search") + 1
        if query_idx < len(sys.argv):
            query = sys.argv[query_idx]
            print(f"\nSearching: {query}\n")
            results = await search_course(query)
            for r in results:
                content = str(r.content) if hasattr(r, "content") else str(r)
                print(f"  - {content[:200]}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
