from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

UNKNOWN_NAMES = {
    "unknown", "speaker", "participant", "guest", "anonymous", "null", "none",
    "неизвестно", "участник", "гость", "аноним",
}


@dataclass(frozen=True, slots=True)
class NameObservation:
    speaker_key: str
    name: str
    confidence: float
    timestamp: float


def clean_visible_name(name: str) -> str | None:
    value = unicodedata.normalize("NFKC", name or "")
    value = "".join(char for char in value if unicodedata.category(char)[0] != "C")
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n|•·-–—")
    value = re.sub(r"\s*\((?:you|вы)\)\s*$", "", value, flags=re.I).strip()
    normalized = normalize_name(value)
    if not value or len(value) > 80 or normalized in UNKNOWN_NAMES:
        return None
    if not any(char.isalpha() for char in value):
        return None
    return value


def normalize_name(name: str) -> str:
    value = unicodedata.normalize("NFKC", name or "").casefold()
    value = re.sub(r"[^\w\s'-]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def choose_frame_segments(segments: list[dict], limit: int = 5) -> list[dict]:
    """Choose long, time-diverse segments instead of several adjacent frames."""
    eligible = [s for s in segments if float(s["end"]) - float(s["start"]) >= 1.2]
    if len(eligible) <= limit:
        return sorted(eligible, key=lambda s: float(s["start"]))
    ordered = sorted(eligible, key=lambda s: float(s["start"]))
    selected: list[dict] = []
    used: set[int] = set()
    for position in range(limit):
        index = round(position * (len(ordered) - 1) / (limit - 1))
        candidates = sorted(
            range(len(ordered)),
            key=lambda i: (abs(i - index), -(float(ordered[i]["end"]) - float(ordered[i]["start"]))),
        )
        chosen = next(i for i in candidates if i not in used)
        used.add(chosen)
        selected.append(ordered[chosen])
    return sorted(selected, key=lambda s: float(s["start"]))


def reconcile_observations(
    observations: Iterable[NameObservation], minimum_confidence: float = 0.70
) -> tuple[dict[str, str], dict[str, dict]]:
    """Accept only repeated or exceptionally strong, unambiguous visible-name evidence."""
    grouped: dict[str, list[NameObservation]] = defaultdict(list)
    for item in observations:
        cleaned = clean_visible_name(item.name)
        if cleaned and item.confidence >= minimum_confidence:
            grouped[item.speaker_key].append(
                NameObservation(item.speaker_key, cleaned, min(1.0, max(0.0, item.confidence)), item.timestamp)
            )

    mapping: dict[str, str] = {}
    audit: dict[str, dict] = {}
    for speaker_key, items in grouped.items():
        candidates: dict[str, list[NameObservation]] = defaultdict(list)
        for item in items:
            candidates[normalize_name(item.name)].append(item)
        ranked = sorted(
            candidates.items(),
            key=lambda pair: (sum(x.confidence for x in pair[1]), len(pair[1]), max(x.confidence for x in pair[1])),
            reverse=True,
        )
        best_key, best = ranked[0]
        best_score = sum(x.confidence for x in best)
        runner_score = sum(x.confidence for x in ranked[1][1]) if len(ranked) > 1 else 0.0
        distinct_times = len({round(x.timestamp, 1) for x in best})
        repeated = distinct_times >= 2 and best_score / len(best) >= 0.72
        exceptional = len(best) == 1 and best[0].confidence >= 0.96
        unambiguous = not runner_score or best_score - runner_score >= 0.50
        accepted = (repeated or exceptional) and unambiguous
        display = max(best, key=lambda x: x.confidence).name
        audit[speaker_key] = {
            "accepted": accepted,
            "candidate": display,
            "evidence_count": distinct_times,
            "score": round(best_score, 3),
            "runner_up_score": round(runner_score, 3),
            "reason": "consensus" if accepted else "insufficient_or_ambiguous_visible_text",
        }
        if accepted and best_key:
            mapping[speaker_key] = display
    return mapping, audit
