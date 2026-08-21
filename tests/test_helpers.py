from pathlib import Path

import pytest

from flexthatcall_core.helpers import fmt_time, render_transcript, safe_json, split_text_lines, validate_source


def test_fmt_time_clamps_and_formats() -> None:
    assert fmt_time(-4) == "00:00:00"
    assert fmt_time(3661.9) == "01:01:01"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"ok": true}', {"ok": True}),
        ('```json\n{"name": "Иван"}\n```', {"name": "Иван"}),
        ('Result follows: {"confidence": 0.9} thanks', {"confidence": 0.9}),
        ('[1, 2, 3]', {}),
    ],
)
def test_safe_json(text: str, expected: dict) -> None:
    assert safe_json(text) == expected


def test_split_text_lines_preserves_normal_lines() -> None:
    text = "one\ntwo\nthree\nfour"
    batches = split_text_lines(text, 9)
    assert batches == ["one\ntwo", "three", "four"]
    assert "\n".join(batches) == text


def test_split_text_lines_rejects_invalid_size() -> None:
    with pytest.raises(ValueError):
        split_text_lines("text", 0)


def test_render_transcript_uses_name_only_when_mapped() -> None:
    segments = [
        {"start": 1, "end": 3, "speaker_key": "C01-A", "text": "Привет"},
        {"start": 4, "end": 5, "speaker_key": "C01-B", "text": "Да"},
    ]
    rendered = render_transcript(segments, {"C01-A": "Иван Петров"})
    assert "Иван Петров: Привет" in rendered
    assert "C01-B: Да" in rendered


def test_validate_source(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    assert validate_source(missing, {".mp4"}) == "Choose an existing recording file."
    unsupported = tmp_path / "call.txt"
    unsupported.write_text("x", encoding="utf-8")
    assert "Unsupported file type" in (validate_source(unsupported, {".mp4"}) or "")
    supported = tmp_path / "call.MP4"
    supported.write_bytes(b"x")
    assert validate_source(supported, {".mp4"}) is None
