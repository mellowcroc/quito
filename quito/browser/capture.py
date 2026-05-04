from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from ..models import FlowStep, Spec


async def capture_screenshots_and_video(
    spec: Spec,
    app_url: str,
    screenshots_dir: Path,
    video_path: Path,
) -> list[Path]:
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    video_dir = video_path.parent
    video_dir.mkdir(parents=True, exist_ok=True)

    screenshot_paths = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 720},
        )
        page = await context.new_page()

        await page.goto(app_url, wait_until="networkidle", timeout=30000)
        idx = 1
        path = screenshots_dir / f"{idx:03d}_initial.png"
        await page.screenshot(path=str(path))
        screenshot_paths.append(path)

        if spec.user_flows:
            for flow in spec.user_flows:
                for step in flow.steps:
                    idx += 1
                    await _execute_step(page, step, app_url)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    path = screenshots_dir / f"{idx:03d}_{step.id}.png"
                    await page.screenshot(path=str(path))
                    screenshot_paths.append(path)
        else:
            for link in await page.query_selector_all("a[href]"):
                href = await link.get_attribute("href")
                if not href or href.startswith("#") or href.startswith("javascript"):
                    continue
                if href.startswith("/") or href.startswith(app_url):
                    idx += 1
                    await link.click()
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    path = screenshots_dir / f"{idx:03d}_page.png"
                    await page.screenshot(path=str(path))
                    screenshot_paths.append(path)
                    await page.go_back()
                    await page.wait_for_load_state("networkidle", timeout=10000)
                if idx >= 10:
                    break

        await context.close()
        await browser.close()

    videos = list(video_dir.glob("*.webm"))
    if videos and videos[0] != video_path:
        videos[0].rename(video_path)

    return screenshot_paths


async def _execute_step(page, step: FlowStep, app_url: str):
    match step.action:
        case "navigate":
            url = step.value or "/"
            if url.startswith("/"):
                url = app_url.rstrip("/") + url
            await page.goto(url, wait_until="networkidle", timeout=15000)
        case "click":
            if step.selector:
                await page.click(step.selector, timeout=5000)
            else:
                text = step.value or step.description
                await page.get_by_text(text).first.click(timeout=5000)
        case "type":
            if step.selector and step.value:
                await page.fill(step.selector, step.value, timeout=5000)
        case "wait":
            await asyncio.sleep(2)
        case "assert":
            pass


def run_capture(spec: Spec, app_url: str, screenshots_dir: Path, video_path: Path) -> list[Path]:
    return asyncio.run(capture_screenshots_and_video(spec, app_url, screenshots_dir, video_path))
