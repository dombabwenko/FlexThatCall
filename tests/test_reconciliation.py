from flexthatcall_core.reconciliation import (
    NameObservation,
    choose_frame_segments,
    clean_visible_name,
    normalize_name,
    reconcile_observations,
)


def observation(name: str, confidence: float, timestamp: float, speaker: str = "C01-A") -> NameObservation:
    return NameObservation(speaker, name, confidence, timestamp)


def test_name_cleanup_normalizes_meet_self_suffix() -> None:
    assert clean_visible_name("  Иван   Петров (Вы) ") == "Иван Петров"
    assert normalize_name("ИВАН ПЕТРОВ") == normalize_name("Иван Петров")
    assert clean_visible_name("Unknown") is None
    assert clean_visible_name("1234") is None


def test_repeated_consistent_visible_name_is_accepted() -> None:
    mapping, audit = reconcile_observations([
        observation("Иван Петров", 0.82, 10.0),
        observation("ИВАН ПЕТРОВ", 0.86, 40.0),
    ])
    assert mapping["C01-A"] in {"Иван Петров", "ИВАН ПЕТРОВ"}
    assert audit["C01-A"]["accepted"] is True
    assert audit["C01-A"]["evidence_count"] == 2


def test_single_merely_good_observation_stays_anonymous() -> None:
    mapping, audit = reconcile_observations([observation("Анна", 0.95, 10.0)])
    assert mapping == {}
    assert audit["C01-A"]["accepted"] is False


def test_single_exceptional_observation_can_be_accepted() -> None:
    mapping, _ = reconcile_observations([observation("Анна", 0.97, 10.0)])
    assert mapping == {"C01-A": "Анна"}


def test_competing_names_are_rejected_as_ambiguous() -> None:
    mapping, audit = reconcile_observations([
        observation("Анна", 0.82, 10.0),
        observation("Анна", 0.82, 30.0),
        observation("Мария", 0.80, 50.0),
        observation("Мария", 0.80, 70.0),
    ])
    assert mapping == {}
    assert audit["C01-A"]["reason"] == "insufficient_or_ambiguous_visible_text"


def test_frame_selection_is_time_diverse_and_filters_short_segments() -> None:
    segments = [
        {"start": index * 10.0, "end": index * 10.0 + (0.5 if index == 3 else 3.0)}
        for index in range(10)
    ]
    chosen = choose_frame_segments(segments, limit=3)
    assert len(chosen) == 3
    assert chosen[0]["start"] == 0.0
    assert chosen[-1]["start"] == 90.0
    assert all(item["end"] - item["start"] >= 1.2 for item in chosen)
