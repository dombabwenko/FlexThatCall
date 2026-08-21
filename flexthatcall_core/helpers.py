from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


def fmt_time(seconds: float) -> str:
    total = max(0, int(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def safe_json(value: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating a Markdown fence or surrounding prose."""
    text = (value or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def response_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(value, dict):
        return value
    return safe_json(str(value))


def render_transcript(segments: Iterable[dict[str, Any]], names: dict[str, str]) -> str:
    lines: list[str] = []
    for segment in segments:
        speaker_key = str(segment.get("speaker_key", "Unknown"))
        speaker = names.get(speaker_key, speaker_key)
        lines.append(
            f"[{fmt_time(float(segment.get('start', 0)))}–"
            f"{fmt_time(float(segment.get('end', 0)))}] {speaker}: "
            f"{str(segment.get('text', '')).strip()}"
        )
    return "\n".join(lines)


def split_text_lines(text: str, max_chars: int) -> list[str]:
    """Split text on line boundaries without dropping oversized lines."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    batches: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in text.splitlines():
        needed = len(line) + (1 if current else 0)
        if current and current_size + needed > max_chars:
            batches.append("\n".join(current))
            current = []
            current_size = 0
        if len(line) > max_chars:
            if current:
                batches.append("\n".join(current))
                current = []
                current_size = 0
            batches.extend(line[i : i + max_chars] for i in range(0, len(line), max_chars))
            continue
        current.append(line)
        current_size += needed
    if current:
        batches.append("\n".join(current))
    return batches or ([""] if text == "" else [])


def validate_source(path: Path, supported: set[str]) -> str | None:
    if not path.exists() or not path.is_file():
        return "Choose an existing recording file."
    if path.suffix.lower() not in supported:
        allowed = ", ".join(sorted(supported))
        return f"Unsupported file type '{path.suffix}'. Supported types: {allowed}"
    return None
