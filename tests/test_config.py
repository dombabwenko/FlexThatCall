import json

from flexthatcall_core.config import AppSettings, load_settings, save_settings


def test_settings_round_trip_without_secret_fields(tmp_path) -> None:
    path = tmp_path / "settings.json"
    expected = AppSettings(
        last_source="C:/Calls/call.mp4",
        output_dir="C:/Calls/output",
        use_video_names=False,
        remember_key=True,
        summary_model="summary-model",
        vision_model="vision-model",
    )
    save_settings(expected, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in raw
    assert "key" not in raw
    assert load_settings(path) == expected


def test_corrupt_settings_fall_back_to_defaults(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("not json", encoding="utf-8")
    assert load_settings(path) == AppSettings()


def test_unknown_settings_are_ignored(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"last_source": "call.mp4", "api_key": "must-not-load"}', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.last_source == "call.mp4"
    assert not hasattr(loaded, "api_key")
