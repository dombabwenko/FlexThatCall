from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

APP_NAME = "FlexThatCall"


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def log_dir() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or app_data_dir())
        return base / APP_NAME / "logs"
    return app_data_dir() / "logs"


@dataclass(slots=True)
class AppSettings:
    last_source: str = ""
    output_dir: str = ""
    use_video_names: bool = True
    remember_key: bool = True
    summary_model: str = "gpt-5-mini"
    vision_model: str = "gpt-5-mini"


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or settings_path()
    try:
        raw: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()
    allowed = {field.name for field in fields(AppSettings)}
    clean = {key: value for key, value in raw.items() if key in allowed}
    try:
        return AppSettings(**clean)
    except TypeError:
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    """Persist non-secret settings atomically. API keys are intentionally absent."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
