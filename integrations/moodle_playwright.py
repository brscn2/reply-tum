"""Real Moodle scraper via Playwright.

Logs in through TUM Shibboleth SSO, saves the session for reuse,
scrapes the dashboard for active courses (filterable by semester),
and downloads all course materials via Download Center as numbered zips.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import BrowserContext, Page, async_playwright

log = structlog.get_logger()

MOODLE_BASE = "https://www.moodle.tum.de"
LOGIN_URL = f"{MOODLE_BASE}/login/index.php"
DASHBOARD_URL = f"{MOODLE_BASE}/my/"

SESSION_DIR = Path(os.getenv("SCHATTEN_SESSION_DIR", ".sessions"))
SESSION_FILE = SESSION_DIR / "moodle_state.json"
DOWNLOAD_DIR = Path(os.getenv("SCHATTEN_DOWNLOAD_DIR", "downloads"))

TUM_USERNAME = os.getenv("TUM_USERNAME", "")
TUM_PASSWORD = os.getenv("TUM_PASSWORD", "")


# ---------------------------------------------------------------------------
# Browser / session management
# ---------------------------------------------------------------------------

async def _ensure_browser_context() -> tuple[Any, BrowserContext]:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=os.getenv("SCHATTEN_HEADLESS", "1") == "1")

    if SESSION_FILE.exists():
        log.info("moodle.session.reuse", path=str(SESSION_FILE))
        context = await browser.new_context(
            storage_state=str(SESSION_FILE),
            accept_downloads=True,
        )
    else:
        context = await browser.new_context(accept_downloads=True)

    return pw, context


async def _save_session(context: BrowserContext) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(SESSION_FILE))
    log.info("moodle.session.saved", path=str(SESSION_FILE))


async def _login(context: BrowserContext) -> None:
    page = await context.new_page()
    log.info("moodle.login.start")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")

    tum_login_link = page.locator("a:has-text('TUM Login')").first
    try:
        await tum_login_link.wait_for(state="visible", timeout=5000)
    except Exception:
        tum_login_link = page.locator(
            "xpath=/html/body/div[2]/div[2]/div/div/div/div/div/div"
            "/div/div[1]/div[3]/div[2]/div/ul/li[1]/dl/dt/a"
        )
    await tum_login_link.click()

    await page.wait_for_url("**/login.tum.de/**", timeout=15000)
    log.info("moodle.login.shibboleth_page")

    await page.fill("#username", TUM_USERNAME)
    await page.fill("#password", TUM_PASSWORD)
    await page.click("#btnLogin")

    await page.wait_for_url(f"{MOODLE_BASE}/**", timeout=30000)
    log.info("moodle.login.success")

    await _save_session(context)
    await page.close()


async def _ensure_logged_in(context: BrowserContext) -> None:
    page = await context.new_page()
    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded")

    if "login" in page.url.lower():
        log.info("moodle.session.expired")
        await page.close()
        await _login(context)
    else:
        log.info("moodle.session.valid")
        await page.close()


# ---------------------------------------------------------------------------
# Semester filter
# ---------------------------------------------------------------------------

async def get_semesters(context: BrowserContext) -> list[dict[str, str]]:
    """Return available semesters from the dashboard filter dropdown."""
    page = await context.new_page()
    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded")

    semesters: list[dict[str, str]] = []
    select = page.locator("#coc-filterterm")

    if await select.count() > 0:
        options = select.locator("option")
        count = await options.count()
        for i in range(count):
            opt = options.nth(i)
            value = await opt.get_attribute("value") or ""
            label = (await opt.inner_text()).strip()
            if label:
                semesters.append({"value": value, "label": label})

    log.info("moodle.semesters.fetched", count=len(semesters))
    await page.close()
    return semesters


# ---------------------------------------------------------------------------
# Course listing
# ---------------------------------------------------------------------------

async def _select_semester(page: Page, semester_value: str) -> None:
    """Select a semester in the dashboard filter and wait for update."""
    select = page.locator("#coc-filterterm")
    if await select.count() == 0:
        log.warning("moodle.semester.filter_not_found")
        return
    await select.select_option(semester_value)
    await page.wait_for_timeout(2000)
    log.info("moodle.semester.selected", value=semester_value)


async def get_courses(semester: str | None = None) -> list[dict[str, Any]]:
    """Fetch courses from dashboard, optionally filtered by semester."""
    pw, context = await _ensure_browser_context()
    try:
        await _ensure_logged_in(context)

        page = await context.new_page()
        await page.goto(DASHBOARD_URL, wait_until="domcontentloaded")

        if semester is not None:
            await _select_semester(page, semester)

        courses: list[dict[str, Any]] = []
        course_links = page.locator("a[href*='/course/view.php?id=']")
        count = await course_links.count()
        seen_ids: set[str] = set()

        for i in range(count):
            link = course_links.nth(i)
            href = await link.get_attribute("href") or ""
            if "id=" not in href:
                continue
            course_id = href.split("id=")[-1].split("&")[0]
            if course_id in seen_ids:
                continue
            seen_ids.add(course_id)
            name = (await link.inner_text()).strip()
            if not name or len(name) < 3:
                continue
            courses.append({
                "moodle_id": course_id,
                "name": name,
                "url": href,
            })

        log.info("moodle.courses.fetched", count=len(courses))
        await page.close()
        await _save_session(context)
        return courses
    finally:
        await context.close()
        await pw.stop()


# ---------------------------------------------------------------------------
# Download Center — download all materials for one course
# ---------------------------------------------------------------------------

def _safe_dirname(name: str) -> str:
    """Sanitize a course name into a filesystem-safe directory name."""
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ ")
    return "".join(c if c in keep else "_" for c in name).strip()[:120]


async def _download_course_zip(page: Page, course: dict[str, Any]) -> Path | None:
    """Navigate to Download Center and download the numbered zip."""
    course_url = f"{MOODLE_BASE}/course/view.php?id={course['moodle_id']}"
    await page.goto(course_url, wait_until="domcontentloaded")
    log.info("moodle.download.course_page", course=course["name"])

    # --- Find "Download Center" in the course nav / more menu ---
    dc_link = page.locator("a:has-text('Download center'), a:has-text('Download Center')").first
    try:
        await dc_link.wait_for(state="visible", timeout=3000)
    except Exception:
        more_menu = page.locator(
            ".moremenu .dropdownmoremenu a.dropdown-toggle, "
            "a[data-toggle='dropdown']:has-text('Mehr'), "
            "a[data-toggle='dropdown']:has-text('More')"
        ).first
        try:
            await more_menu.wait_for(state="visible", timeout=2000)
            await more_menu.click()
            await page.wait_for_timeout(500)
            dc_link = page.locator(
                ".dropdown-menu a:has-text('Download center'), "
                ".dropdown-menu a:has-text('Download Center')"
            ).first
            await dc_link.wait_for(state="visible", timeout=3000)
        except Exception:
            log.warning("moodle.download.no_download_center", course=course["name"])
            return None

    await dc_link.click()
    await page.wait_for_load_state("domcontentloaded")
    log.info("moodle.download.center_opened", course=course["name"])

    # --- Ensure all section/resource checkboxes are checked ---
    checkboxes = page.locator("input[type='checkbox'][name^='item_']")
    cb_count = await checkboxes.count()
    for i in range(cb_count):
        cb = checkboxes.nth(i)
        if not await cb.is_checked():
            await cb.check()
    log.info("moodle.download.all_checked", course=course["name"], count=cb_count)

    # --- Check "Dateien und Ordner durchnummerieren" ---
    numbering_cb = page.locator("#id_addnumbering")
    if await numbering_cb.count() > 0 and not await numbering_cb.is_checked():
        await numbering_cb.check()
        log.info("moodle.download.numbering_enabled", course=course["name"])

    # --- Click "ZIP-Archiv erstellen" ---
    submit_btn = page.locator("#id_submitbutton")
    try:
        await submit_btn.wait_for(state="visible", timeout=3000)
    except Exception:
        log.warning("moodle.download.no_submit_button", course=course["name"])
        return None

    if cb_count == 0:
        log.warning("moodle.download.no_files", course=course["name"])
        return None

    course_dir = DOWNLOAD_DIR / _safe_dirname(course["name"])
    course_dir.mkdir(parents=True, exist_ok=True)

    async with page.expect_download(timeout=120000) as download_info:
        await submit_btn.click()

    download = await download_info.value
    dest = course_dir / (download.suggested_filename or f"{course['moodle_id']}.zip")
    await download.save_as(str(dest))
    log.info("moodle.download.saved", course=course["name"], path=str(dest))
    return dest


# ---------------------------------------------------------------------------
# Public API: download all courses
# ---------------------------------------------------------------------------

async def download_all_courses(semester: str | None = None) -> list[dict[str, Any]]:
    """Download materials for all courses via Download Center.

    Returns a list of dicts with course info and the path to the zip.
    """
    pw, context = await _ensure_browser_context()
    try:
        await _ensure_logged_in(context)

        # --- Fetch courses ---
        page = await context.new_page()
        await page.goto(DASHBOARD_URL, wait_until="domcontentloaded")

        if semester is not None:
            await _select_semester(page, semester)

        courses: list[dict[str, Any]] = []
        course_links = page.locator("a[href*='/course/view.php?id=']")
        count = await course_links.count()
        seen_ids: set[str] = set()

        for i in range(count):
            link = course_links.nth(i)
            href = await link.get_attribute("href") or ""
            if "id=" not in href:
                continue
            course_id = href.split("id=")[-1].split("&")[0]
            if course_id in seen_ids:
                continue
            seen_ids.add(course_id)
            name = (await link.inner_text()).strip()
            if not name or len(name) < 3:
                continue
            courses.append({"moodle_id": course_id, "name": name, "url": href})

        await page.close()
        log.info("moodle.download.courses_found", count=len(courses))

        # --- Download each course ---
        results: list[dict[str, Any]] = []
        for course in courses:
            page = await context.new_page()
            try:
                zip_path = await _download_course_zip(page, course)
                results.append({
                    **course,
                    "zip_path": str(zip_path) if zip_path else None,
                    "status": "downloaded" if zip_path else "skipped",
                })
            except Exception as exc:
                log.exception("moodle.download.error", course=course["name"])
                results.append({**course, "zip_path": None, "status": f"error: {exc}"})
            finally:
                await page.close()

        await _save_session(context)
        return results
    finally:
        await context.close()
        await pw.stop()


# ---------------------------------------------------------------------------
# get_uploads — kept for agent compatibility
# ---------------------------------------------------------------------------

async def get_uploads(moodle_course_id: str) -> list[dict[str, Any]]:
    """Fetch recent uploads/resources from a specific course page."""
    pw, context = await _ensure_browser_context()
    try:
        await _ensure_logged_in(context)

        page = await context.new_page()
        course_url = f"{MOODLE_BASE}/course/view.php?id={moodle_course_id}"
        await page.goto(course_url, wait_until="domcontentloaded")

        uploads: list[dict[str, Any]] = []
        resource_links = page.locator(
            "a[href*='/mod/resource/view.php'], "
            "a[href*='/mod/folder/view.php'], "
            "a[href*='/pluginfile.php']"
        )
        count = await resource_links.count()
        seen_urls: set[str] = set()

        for i in range(count):
            link = resource_links.nth(i)
            href = await link.get_attribute("href") or ""
            if href in seen_urls or not href:
                continue
            seen_urls.add(href)
            name = (await link.inner_text()).strip()
            if not name or len(name) < 2:
                continue
            uploads.append({
                "filename": name,
                "url": href,
                "moodle_course_id": moodle_course_id,
                "content": None,
            })

        log.info("moodle.uploads.fetched", course_id=moodle_course_id, count=len(uploads))
        await page.close()
        await _save_session(context)
        return uploads
    finally:
        await context.close()
        await pw.stop()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    import sys

    if not TUM_USERNAME or not TUM_PASSWORD:
        print("Set TUM_USERNAME and TUM_PASSWORD in .env or environment")
        return

    print(f"Logging in as {TUM_USERNAME}...\n")

    # --- Show semesters ---
    pw, context = await _ensure_browser_context()
    try:
        await _ensure_logged_in(context)
        semesters = await get_semesters(context)
        await _save_session(context)
    finally:
        await context.close()
        await pw.stop()

    # CLI flags: --semester VALUE  --yes (skip confirmation)
    semester: str | None = None
    auto_yes = "--yes" in sys.argv

    for i, arg in enumerate(sys.argv):
        if arg == "--semester" and i + 1 < len(sys.argv):
            semester = sys.argv[i + 1]

    if semesters:
        print("Available semesters:")
        for i, s in enumerate(semesters):
            print(f"  [{i}] {s['label']}  (value={s['value']})")
        print()

        if semester is None and sys.stdin.isatty():
            choice = input("Select semester number (or Enter for all): ").strip()
            semester = semesters[int(choice)]["value"] if choice.isdigit() else None
        elif semester is not None:
            print(f"Using semester: {semester}")
    else:
        print("No semester filter found, fetching all courses.\n")

    # --- List courses ---
    courses = await get_courses(semester=semester)
    print(f"\nFound {len(courses)} courses:\n")
    for c in courses:
        print(f"  [{c['moodle_id']}] {c['name']}")

    # --- Download all ---
    if not auto_yes and sys.stdin.isatty():
        confirm = input("\nDownload all course materials? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    print(f"\nDownloading to {DOWNLOAD_DIR.resolve()}...\n")
    results = await download_all_courses(semester=semester)

    print("\n--- Results ---\n")
    for r in results:
        icon = "OK" if r["status"] == "downloaded" else "SKIP"
        print(f"  [{icon}] {r['name']}")
        if r["zip_path"]:
            print(f"       -> {r['zip_path']}")
        else:
            print(f"       -> {r['status']}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
