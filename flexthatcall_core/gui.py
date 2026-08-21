from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
import tkinter as tk

import keyring
from openai import APIConnectionError, AuthenticationError, BadRequestError, PermissionDeniedError, RateLimitError

from .config import APP_NAME, AppSettings, load_settings, save_settings
from .engine import (
    SUPPORTED_EXTENSIONS,
    FlexThatCallEngine,
    ProcessResult,
    ProcessingCancelled,
    ProcessingError,
    ProgressEvent,
)
from .helpers import validate_source
from .logging_setup import setup_logging

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None

KEYRING_SERVICE = "FlexThatCall/OpenAI"
KEYRING_ACCOUNT = "default"


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return "OpenAI rejected the API key. Check the key and its project access, then try again."
    if isinstance(exc, PermissionDeniedError):
        return "This API project does not have permission to use one of the selected models."
    if isinstance(exc, RateLimitError):
        return "The OpenAI API rate or spending limit was reached. Wait or check the project limits, then retry."
    if isinstance(exc, APIConnectionError):
        return "Could not connect to the OpenAI API. Check the internet connection, proxy, and firewall."
    if isinstance(exc, BadRequestError):
        return f"OpenAI could not process this request: {exc.message}"
    if isinstance(exc, (ProcessingError, ProcessingCancelled)):
        return str(exc)
    return f"Processing failed: {exc}"


class FlexThatCallApp:
    def __init__(self, root) -> None:
        self.root = root
        self.logger, self.log_path = setup_logging()
        self.settings = load_settings()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False

        root.title(APP_NAME)
        root.geometry("860x650")
        root.minsize(720, 570)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.file_var = StringVar(value=self.settings.last_source)
        self.output_var = StringVar(value=self.settings.output_dir)
        self.key_var = StringVar(value=self._load_key())
        self.use_video_names_var = BooleanVar(value=self.settings.use_video_names)
        self.remember_key_var = BooleanVar(value=self.settings.remember_key)
        self.show_key_var = BooleanVar(value=False)
        self.status_var = StringVar(value="Ready. Choose a recording to begin.")
        self.progress_var = tk.DoubleVar(value=0)

        self._build_ui()
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=BOTH, expand=True)
        ttk.Label(outer, text=APP_NAME, font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Russian call transcription, conservative visible-name attribution, and English minutes",
        ).pack(anchor="w", pady=(0, 14))

        recording = ttk.LabelFrame(outer, text="1. Recording and output", padding=12)
        recording.pack(fill=X)
        source_row = ttk.Frame(recording)
        source_row.pack(fill=X)
        ttk.Label(source_row, text="Recording:", width=11).pack(side=LEFT)
        ttk.Entry(source_row, textvariable=self.file_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(source_row, text="Browse…", command=self.pick_file).pack(side=RIGHT, padx=(8, 0))
        output_row = ttk.Frame(recording)
        output_row.pack(fill=X, pady=(8, 0))
        ttk.Label(output_row, text="Save to:", width=11).pack(side=LEFT)
        ttk.Entry(output_row, textvariable=self.output_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(output_row, text="Choose…", command=self.pick_output).pack(side=RIGHT, padx=(8, 0))
        self.drop_label = ttk.Label(recording, text="Tip: drag and drop one supported audio/video file into this box.")
        self.drop_label.pack(anchor="w", pady=(8, 0))
        if DND_FILES and hasattr(recording, "drop_target_register"):
            recording.drop_target_register(DND_FILES)
            recording.dnd_bind("<<Drop>>", self.on_drop)

        api = ttk.LabelFrame(outer, text="2. OpenAI API", padding=12)
        api.pack(fill=X, pady=(12, 0))
        api_row = ttk.Frame(api)
        api_row.pack(fill=X)
        ttk.Label(api_row, text="API key:", width=11).pack(side=LEFT)
        self.key_entry = ttk.Entry(api_row, textvariable=self.key_var, show="•")
        self.key_entry.pack(side=LEFT, fill=X, expand=True)
        ttk.Checkbutton(api_row, text="Show", variable=self.show_key_var, command=self.toggle_key).pack(side=LEFT, padx=(8, 0))
        key_actions = ttk.Frame(api)
        key_actions.pack(fill=X, pady=(7, 0))
        ttk.Checkbutton(
            key_actions,
            text="Remember securely in Windows Credential Manager (never in settings.json)",
            variable=self.remember_key_var,
        ).pack(side=LEFT)
        ttk.Button(key_actions, text="Forget saved key", command=self.forget_key).pack(side=RIGHT)

        attribution = ttk.LabelFrame(outer, text="3. Speaker attribution", padding=12)
        attribution.pack(fill=X, pady=(12, 0))
        ttk.Checkbutton(
            attribution,
            text="For video, try to read the visible Google Meet active-speaker name",
            variable=self.use_video_names_var,
        ).pack(anchor="w")
        ttk.Label(
            attribution,
            text="Faces and voices are never used for identity. Uncertain results stay anonymous (for example, C01-A).",
            wraplength=800,
        ).pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=(16, 0))
        self.run_button = ttk.Button(actions, text="Transcribe & summarize", command=self.start)
        self.run_button.pack(side=LEFT)
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="Open output folder", command=self.open_output).pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="Open log folder", command=self.open_log_folder).pack(side=RIGHT)

        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100, variable=self.progress_var)
        self.progress.pack(fill=X, pady=(16, 7))
        ttk.Label(outer, textvariable=self.status_var).pack(anchor="w")

        log_frame = ttk.LabelFrame(outer, text="Processing log", padding=8)
        log_frame.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill="y")
        self.log_text.pack(fill=BOTH, expand=True)

    def _load_key(self) -> str:
        environment_key = os.getenv("OPENAI_API_KEY", "").strip()
        if environment_key:
            return environment_key
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or ""
        except Exception as exc:
            self.logger.warning("Could not read API key from keyring: %s", exc)
            return ""

    def toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key_var.get() else "•")

    def forget_key(self) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            messagebox.showwarning(APP_NAME, f"Could not remove the saved key: {exc}")
            return
        self.key_var.set("")
        messagebox.showinfo(APP_NAME, "The saved API key was removed from the operating system keyring.")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(END, message + "\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def _default_output(self, source: Path) -> str:
        return str(source.resolve().parent / "FlexThatCall_Output")

    def set_source(self, raw_path: str) -> None:
        previous = self.file_var.get().strip()
        old_default = self._default_output(Path(previous)) if previous else ""
        source = Path(raw_path).expanduser()
        self.file_var.set(str(source))
        if not self.output_var.get().strip() or self.output_var.get().strip() == old_default:
            self.output_var.set(self._default_output(source))
        self._save_settings()

    def pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose call recording",
            filetypes=[
                ("Audio and video", "*.mp3 *.mp4 *.m4a *.wav *.webm *.mpeg *.mpga *.ogg *.flac *.mov *.mkv"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.set_source(path)

    def pick_output(self) -> None:
        initial = self.output_var.get().strip() or None
        path = filedialog.askdirectory(title="Choose output folder", initialdir=initial)
        if path:
            self.output_var.set(path)
            self._save_settings()

    def on_drop(self, event) -> None:
        try:
            paths = self.root.tk.splitlist(event.data)
            if len(paths) != 1:
                messagebox.showwarning(APP_NAME, "Drop one recording at a time.")
                return
            self.set_source(paths[0])
        except Exception as exc:
            self.logger.warning("Could not parse dropped file: %s", exc)

    @staticmethod
    def _open_folder(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif shutil.which("open"):
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def open_output(self) -> None:
        raw = self.output_var.get().strip()
        if not raw:
            messagebox.showinfo(APP_NAME, "Choose a recording or output folder first.")
            return
        try:
            self._open_folder(Path(raw).expanduser())
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open the output folder: {exc}")

    def open_log_folder(self) -> None:
        try:
            self._open_folder(self.log_path.parent)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open the log folder: {exc}")

    def _current_settings(self) -> AppSettings:
        return AppSettings(
            last_source=self.file_var.get().strip(),
            output_dir=self.output_var.get().strip(),
            use_video_names=bool(self.use_video_names_var.get()),
            remember_key=bool(self.remember_key_var.get()),
            summary_model=os.getenv("FLEXTHATCALL_SUMMARY_MODEL", self.settings.summary_model),
            vision_model=os.getenv("FLEXTHATCALL_VISION_MODEL", self.settings.vision_model),
        )

    def _save_settings(self) -> None:
        self.settings = self._current_settings()
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.logger.warning("Could not save settings: %s", exc)

    def start(self) -> None:
        if self.busy:
            return
        source = Path(self.file_var.get().strip()).expanduser()
        problem = validate_source(source, SUPPORTED_EXTENSIONS)
        if problem:
            messagebox.showerror(APP_NAME, problem)
            return
        output_text = self.output_var.get().strip()
        if not output_text:
            self.output_var.set(self._default_output(source))
            output_text = self.output_var.get()
        output_dir = Path(output_text).expanduser()
        api_key = self.key_var.get().strip()
        if not api_key:
            messagebox.showerror(APP_NAME, "Enter an OpenAI API key.")
            return

        self._save_settings()
        if self.remember_key_var.get():
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, api_key)
            except Exception as exc:
                self.logger.warning("Could not save API key to keyring: %s", exc)
                messagebox.showwarning(
                    APP_NAME,
                    "Processing will continue, but the API key could not be saved securely. "
                    "It will not be written to the settings file.",
                )
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
            except Exception:
                pass

        self.busy = True
        self.cancel_event.clear()
        self.progress_var.set(0)
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self._append_log(f"Starting: {source.name}")
        settings = self.settings
        worker = threading.Thread(
            target=self._worker,
            args=(source.resolve(), output_dir.resolve(), api_key, settings),
            daemon=True,
        )
        worker.start()

    def cancel(self) -> None:
        if self.busy:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("Cancellation requested; waiting for the current operation to finish…")

    def _on_progress(self, event: ProgressEvent) -> None:
        self.events.put(("progress", event))

    def _worker(self, source: Path, output_dir: Path, api_key: str, settings: AppSettings) -> None:
        try:
            engine = FlexThatCallEngine(
                api_key=api_key,
                progress_callback=self._on_progress,
                logger=self.logger,
                summary_model=settings.summary_model,
                vision_model=settings.vision_model,
                cancel_event=self.cancel_event,
            )
            result = engine.process(source, output_dir, settings.use_video_names)
            self.events.put(("done", result))
        except ProcessingCancelled as exc:
            self.logger.info(str(exc))
            self.events.put(("cancelled", exc))
        except Exception as exc:
            self.logger.exception("Processing failed")
            self.events.put(("error", exc))

    def _finish_busy(self) -> None:
        self.busy = False
        self.run_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    event = payload
                    assert isinstance(event, ProgressEvent)
                    self.progress_var.set(event.percent)
                    self.status_var.set(event.message)
                    self._append_log(f"{event.percent:3d}%  {event.message}")
                elif kind == "done":
                    result = payload
                    assert isinstance(result, ProcessResult)
                    self._finish_busy()
                    self.progress_var.set(100)
                    self.status_var.set("Finished successfully.")
                    messagebox.showinfo(
                        APP_NAME,
                        "Finished.\n\n"
                        f"Transcript: {result.transcript_path.name}\n"
                        f"Summary: {result.summary_path.name}\n"
                        f"Audit JSON: {result.json_path.name}",
                    )
                elif kind == "cancelled":
                    self._finish_busy()
                    self.status_var.set("Cancelled. No partial result files were written.")
                    self._append_log("Cancelled.")
                elif kind == "error":
                    exc = payload
                    assert isinstance(exc, Exception)
                    self._finish_busy()
                    message = friendly_error(exc)
                    self.status_var.set(message)
                    self._append_log(f"ERROR: {message}")
                    messagebox.showerror(APP_NAME, f"{message}\n\nTechnical details are in:\n{self.log_path}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
            APP_NAME, "Processing is still running. Request cancellation and close the window?"
        ):
            return
        self.cancel_event.set()
        self._save_settings()
        self.root.destroy()


def main() -> None:
    root_class = TkinterDnD.Tk if TkinterDnD else Tk
    root = root_class()
    FlexThatCallApp(root)
    root.mainloop()
