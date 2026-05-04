from __future__ import annotations

import asyncio
import json
from pathlib import Path

import anthropic
from playwright.async_api import async_playwright

from ..models import BugbashFinding, BugbashPersona, Severity, Spec
from ..store import RunStore


def generate_personas(
    spec: Spec,
    count: int = 100,
    model: str = "claude-sonnet-4-6",
) -> list[BugbashPersona]:
    client = anthropic.Anthropic()

    distribution = f"""Distribution across {count} personas:
- {int(count * 0.20)} security (XSS, injection, auth bypass, CSRF, path traversal)
- {int(count * 0.15)} edge cases (empty inputs, unicode, max lengths, boundary values)
- {int(count * 0.15)} accessibility (screen reader, keyboard-only, color contrast, ARIA)
- {int(count * 0.10)} performance (rapid clicks, large payloads, slow network simulation)
- {int(count * 0.10)} mobile/responsive (various viewports, touch events, orientation)
- {int(count * 0.10)} state corruption (back button, tab duplication, stale data, expired sessions)
- {int(count * 0.10)} concurrency (race conditions, double submit, parallel requests)
- {int(count * 0.10)} adversarial UX (confusing flows, misleading states, broken navigation)"""

    prompt = f"""Generate {count} distinct bugbash test personas for this application.

## App Spec
Title: {spec.title}
Description: {spec.description}

Requirements:
{chr(10).join(f'- {r}' for r in spec.requirements)}

{distribution}

Each persona should be unique with a specific testing strategy.

Return a JSON array:
[
  {{
    "id": "sec-001",
    "angle": "security",
    "strategy": "specific strategy description",
    "viewport": {{"width": 1280, "height": 720}}
  }}
]

Return ONLY the JSON array."""

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )

    return _parse_personas(response.content[0].text)


async def run_bugbash(
    personas: list[BugbashPersona],
    spec: Spec,
    app_url: str,
    store: RunStore,
    concurrency: int = 20,
    model: str = "claude-sonnet-4-6",
) -> list[BugbashFinding]:
    sem = asyncio.Semaphore(concurrency)
    all_findings: list[BugbashFinding] = []

    async def run_agent(persona: BugbashPersona):
        async with sem:
            findings = await _bugbash_agent_session(persona, spec, app_url, model)
            for finding in findings:
                store.save_bugbash_finding(finding)
                all_findings.append(finding)

    await asyncio.gather(*[run_agent(p) for p in personas])
    return all_findings


async def _bugbash_agent_session(
    persona: BugbashPersona,
    spec: Spec,
    app_url: str,
    model: str,
    max_actions: int = 50,
) -> list[BugbashFinding]:
    client = anthropic.AsyncAnthropic()
    findings: list[BugbashFinding] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport=persona.viewport,
        )
        page = await context.new_page()

        try:
            await page.goto(app_url, wait_until="networkidle", timeout=30000)
        except Exception:
            return findings

        page_state = await _get_page_state(page)

        for action_num in range(max_actions):
            prompt = f"""You are a bugbash tester with this persona:
Angle: {persona.angle}
Strategy: {persona.strategy}

App: {spec.title} - {spec.description}
Current URL: {page.url}
Page state: {page_state}

Action {action_num + 1}/{max_actions}.

Decide your next action. Return JSON:
{{
  "action": "click" | "type" | "navigate" | "done",
  "selector": "CSS selector or text content",
  "value": "text to type (for type action) or URL (for navigate)",
  "reasoning": "why this action"
}}

If you found a bug, include:
{{
  "action": "report",
  "bug": {{
    "issue": "description",
    "severity": "critical" | "high" | "medium" | "low",
    "reproduction_steps": ["step1", "step2"],
    "category": "{persona.angle}"
  }}
}}

If done testing, return {{"action": "done"}}"""

            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}],
                )
                action = _extract_json_from_text(response.content[0].text)
            except Exception:
                break

            action_type = action.get("action", "done")

            if action_type == "done":
                break

            if action_type == "report":
                bug = action.get("bug", {})
                try:
                    severity = Severity(bug.get("severity", "medium").lower())
                except ValueError:
                    severity = Severity.MEDIUM
                findings.append(BugbashFinding(
                    persona_id=persona.id,
                    issue=bug.get("issue", ""),
                    severity=severity,
                    reproduction_steps=bug.get("reproduction_steps", []),
                    category=bug.get("category", persona.angle),
                ))
                continue

            try:
                match action_type:
                    case "click":
                        selector = action.get("selector", "")
                        try:
                            await page.click(selector, timeout=3000)
                        except Exception:
                            await page.get_by_text(selector).first.click(timeout=3000)
                    case "type":
                        selector = action.get("selector", "")
                        value = action.get("value", "")
                        try:
                            await page.fill(selector, value, timeout=3000)
                        except Exception:
                            pass
                    case "navigate":
                        url = action.get("value", "/")
                        if url.startswith("/"):
                            url = app_url.rstrip("/") + url
                        await page.goto(url, wait_until="networkidle", timeout=15000)

                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            page_state = await _get_page_state(page)

        await context.close()
        await browser.close()

    return findings


async def _get_page_state(page) -> str:
    try:
        title = await page.title()
        text = await page.inner_text("body")
        text = text[:2000]
        return f"Title: {title}\nVisible text (truncated): {text}"
    except Exception:
        return "Unable to read page state"


def deduplicate_findings(
    findings: list[BugbashFinding],
    model: str = "claude-opus-4-7",
) -> tuple[list[dict], str]:
    if not findings:
        return [], "No findings to deduplicate."

    client = anthropic.Anthropic()

    findings_text = json.dumps([f.model_dump() for f in findings], indent=2)

    prompt = f"""Here are {len(findings)} bug reports from {len(set(f.persona_id for f in findings))} testing agents.
Many may be duplicates or variations of the same issue.

Cluster them into unique bugs. For each unique bug:
1. Pick the best reproduction steps
2. Assign a final severity
3. List which persona IDs reported it

Return JSON:
{{
  "clusters": [
    {{
      "id": "bug-001",
      "issue": "description",
      "severity": "critical" | "high" | "medium" | "low",
      "category": "security" | "edge_case" | etc,
      "reproduction_steps": ["step1", "step2"],
      "reported_by": ["persona-id-1", "persona-id-2"]
    }}
  ],
  "summary": "markdown summary of all findings"
}}

Findings:
{findings_text}"""

    response = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    parsed = _extract_json_from_text(response.content[0].text)
    clusters = parsed.get("clusters", [])
    summary = parsed.get("summary", "")
    return clusters, summary


def _parse_personas(text: str) -> list[BugbashPersona]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines)
        for i in range(1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end])

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        bracket_start = text.find("[")
        if bracket_start >= 0:
            bracket_count = 0
            end_pos = bracket_start
            for i in range(bracket_start, len(text)):
                if text[i] == "[":
                    bracket_count += 1
                elif text[i] == "]":
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_pos = i + 1
                        break
            try:
                items = json.loads(text[bracket_start:end_pos])
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(items, list):
        return []

    personas = []
    for item in items:
        viewport = item.get("viewport", {"width": 1280, "height": 720})
        personas.append(BugbashPersona(
            id=item.get("id", f"persona-{len(personas) + 1}"),
            angle=item.get("angle", "general"),
            strategy=item.get("strategy", ""),
            viewport=viewport,
        ))
    return personas


def _extract_json_from_text(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end = len(lines)
        for i in range(1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        json_start = text.find("{")
        if json_start >= 0:
            brace_count = 0
            end_pos = json_start
            for i in range(json_start, len(text)):
                if text[i] == "{":
                    brace_count += 1
                elif text[i] == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i + 1
                        break
            try:
                return json.loads(text[json_start:end_pos])
            except json.JSONDecodeError:
                pass
        return {}
