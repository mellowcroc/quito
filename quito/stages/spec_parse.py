from __future__ import annotations

import re
from pathlib import Path

from ..models import FlowStep, Spec, UserFlow


def parse_spec(path: Path) -> Spec:
    raw = path.read_text()
    title = _extract_title(raw)
    description = _extract_section(raw, "description") or _first_paragraph(raw)
    requirements = _extract_list(raw, "requirements")
    acceptance_criteria = _extract_list(raw, "acceptance criteria")
    user_flows = _extract_user_flows(raw)
    ui_description = _extract_section(raw, "ui") or _extract_section(raw, "design") or ""

    return Spec(
        title=title,
        description=description,
        requirements=requirements,
        acceptance_criteria=acceptance_criteria,
        user_flows=user_flows,
        ui_description=ui_description,
        raw=raw,
    )


def _extract_title(raw: str) -> str:
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("# ").strip()
    return "Untitled"


def _first_paragraph(raw: str) -> str:
    lines = []
    started = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not started:
            if stripped and not stripped.startswith("#"):
                started = True
                lines.append(stripped)
        else:
            if not stripped:
                break
            lines.append(stripped)
    return " ".join(lines)


def _extract_section(raw: str, heading_keyword: str) -> str | None:
    pattern = re.compile(
        rf"^#{1,3}\s+.*{re.escape(heading_keyword)}.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(raw)
    if not match:
        return None

    heading_level = match.group().count("#", 0, match.group().index(" "))
    start = match.end()
    next_heading = re.compile(rf"^#{{{1},{heading_level}}}\s+", re.MULTILINE)
    end_match = next_heading.search(raw, start)
    end = end_match.start() if end_match else len(raw)
    return raw[start:end].strip()


def _extract_list(raw: str, heading_keyword: str) -> list[str]:
    section = _extract_section(raw, heading_keyword)
    if not section:
        return []
    items = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped and re.match(r"^[-*\d.]+\s+", stripped):
            item = re.sub(r"^[-*\d.]+\s+", "", stripped).strip()
            if item:
                items.append(item)
    return items


def _extract_user_flows(raw: str) -> list[UserFlow]:
    section = _extract_section(raw, "user flows") or _extract_section(raw, "flows")
    if not section:
        return []

    flows = []
    current_flow_name = None
    current_steps: list[FlowStep] = []

    for line in section.splitlines():
        stripped = line.strip()
        heading_match = re.match(r"^#{1,4}\s+(.+)$", stripped)
        if heading_match:
            if current_flow_name and current_steps:
                flows.append(UserFlow(name=current_flow_name, steps=current_steps))
            current_flow_name = heading_match.group(1).strip()
            current_steps = []
            continue

        step_match = re.match(r"^[-*\d.]+\s+(.+)$", stripped)
        if step_match and current_flow_name:
            step_text = step_match.group(1).strip()
            step_id = f"step-{len(current_steps) + 1}"
            action, selector, value = _parse_step_text(step_text)
            current_steps.append(FlowStep(
                id=step_id,
                action=action,
                selector=selector,
                value=value,
                description=step_text,
            ))

    if current_flow_name and current_steps:
        flows.append(UserFlow(name=current_flow_name, steps=current_steps))

    return flows


def _parse_step_text(text: str) -> tuple[str, str | None, str | None]:
    lower = text.lower()
    if any(w in lower for w in ["navigate", "go to", "visit", "open"]):
        url_match = re.search(r'["\']?(https?://\S+|/\S*)["\']?', text)
        return "navigate", None, url_match.group(1).strip("\"'") if url_match else "/"
    if any(w in lower for w in ["click", "press", "tap"]):
        return "click", _extract_quoted(text), None
    if any(w in lower for w in ["type", "enter", "input", "fill"]):
        return "type", _extract_quoted(text), _extract_second_quoted(text)
    if any(w in lower for w in ["wait", "loading"]):
        return "wait", None, None
    if any(w in lower for w in ["assert", "verify", "check", "see", "should"]):
        return "assert", None, text
    return "navigate", None, "/"


def _extract_quoted(text: str) -> str | None:
    match = re.search(r'["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


def _extract_second_quoted(text: str) -> str | None:
    matches = re.findall(r'["\']([^"\']+)["\']', text)
    return matches[1] if len(matches) > 1 else None
