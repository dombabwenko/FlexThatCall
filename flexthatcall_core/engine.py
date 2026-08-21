from __future__ import annotations

import base64
import json
import logging
import subprocess
import tempfile
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import imageio_ffmpeg
from openai import OpenAI

from .helpers import render_transcript, response_to_dict, safe_json, split_text_lines
from .reconciliation import NameObservation, choose_frame_segments, reconcile_observations

TRANSCRIBE_MODEL = "gpt-4o-transcribe-diarize"
CHUNK_SECONDS = 20 * 60
AUDIO_BITRATE = "32k"
SUPPORTED_EXTENSIONS = {
    ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".mpga", ".mpeg", ".ogg", ".wav", ".webm"
}
VIDEO_EXTENSIONS = {".mkv", ".mov", ".mp4", ".mpeg", ".webm"}
SUMMARY_BATCH_CHARS = 60_000


class ProcessingCancelled(RuntimeError):
    pass


class ProcessingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    percent: int
    message: str


@dataclass(frozen=True, slots=True)
class ProcessResult:
    transcript_path: Path
    summary_path: Path
    json_path: Path


ProgressCallback = Callable[[ProgressEvent], None]


class FlexThatCallEngine:
    def __init__(
        self,
        api_key: str,
        progress_callback: ProgressCallback,
        logger: logging.Logger,
        summary_model: str = "gpt-5-mini",
        vision_model: str = "gpt-5-mini",
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.client = OpenAI(api_key=api_key, timeout=1800.0, max_retries=2)
        self.progress_callback = progress_callback
        self.logger = logger
        self.summary_model = summary_model
        self.vision_model = vision_model
        self.cancel_event = cancel_event or threading.Event()

    def _progress(self, percent: int, message: str) -> None:
        self.logger.info(message)
        self.progress_callback(ProgressEvent(max(0, min(100, percent)), message))

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise ProcessingCancelled("Processing was cancelled.")

    def _ffmpeg_chunks(self, source: Path, temp_dir: Path) -> list[Path]:
        self._progress(3, "Preparing audio with ffmpeg…")
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        pattern = temp_dir / "chunk_%03d.mp3"
        command = [
            ffmpeg,
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-b:a", AUDIO_BITRATE,
            "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-reset_timestamps", "1",
            str(pattern),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=None)
        except OSError as exc:
            raise ProcessingError(f"Could not start ffmpeg: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "unknown ffmpeg error"
            raise ProcessingError(f"Audio preparation failed. ffmpeg reported: {detail}")
        chunks = sorted(temp_dir.glob("chunk_*.mp3"))
        if not chunks:
            raise ProcessingError("No audio track was found, or ffmpeg produced no audio chunks.")
        self._progress(8, f"Prepared {len(chunks)} audio chunk(s).")
        return chunks

    def transcribe(self, source: Path, temp_dir: Path) -> list[dict]:
        chunks = self._ffmpeg_chunks(source, temp_dir)
        all_segments: list[dict] = []
        for index, chunk in enumerate(chunks):
            self._check_cancelled()
            percent = 10 + round(45 * index / len(chunks))
            self._progress(percent, f"Transcribing audio chunk {index + 1} of {len(chunks)}…")
            with chunk.open("rb") as audio:
                result = self.client.audio.transcriptions.create(
                    model=TRANSCRIBE_MODEL,
                    file=audio,
                    language="ru",
                    response_format="diarized_json",
                    chunking_strategy="auto",
                )
            data = response_to_dict(result)
            raw_segments = data.get("segments")
            if not isinstance(raw_segments, list):
                raise ProcessingError(
                    "The transcription API returned no diarized segments. Update the openai package "
                    "and confirm that the API project can use gpt-4o-transcribe-diarize."
                )
            offset = index * CHUNK_SECONDS
            for raw_segment in raw_segments:
                segment = response_to_dict(raw_segment)
                text = str(segment.get("text", "")).strip()
                if not text:
                    continue
                raw_speaker = str(segment.get("speaker", "?")).strip() or "?"
                relative_start = max(0.0, float(segment.get("start", 0) or 0))
                relative_end = max(relative_start, float(segment.get("end", relative_start) or relative_start))
                all_segments.append({
                    "start": round(relative_start + offset, 3),
                    "end": round(relative_end + offset, 3),
                    "speaker_raw": raw_speaker,
                    "speaker_key": f"C{index + 1:02d}-{raw_speaker}",
                    "text": text,
                    "chunk": index + 1,
                })
        if not all_segments:
            raise ProcessingError("The recording produced an empty transcript.")
        self._progress(55, f"Transcription complete: {len(all_segments)} speaker segment(s).")
        return sorted(all_segments, key=lambda item: (item["start"], item["end"]))

    @staticmethod
    def _encode_frame(frame) -> str | None:
        height, width = frame.shape[:2]
        if width > 1280:
            scale = 1280 / width
            frame = cv2.resize(frame, (1280, int(height * scale)))
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 84])
        if not ok:
            return None
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        return f"data:image/jpeg;base64,{payload}"

    def _frame_data_url(self, capture, timestamp: float) -> str | None:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000)
        ok, frame = capture.read()
        if not ok or frame is None:
            return None
        return self._encode_frame(frame)

    def _read_meet_name(self, data_url: str) -> tuple[str | None, float]:
        prompt = (
            "Inspect this Google Meet recording frame. Read only the participant name visibly attached "
            "to the current active-speaker indicator or active-speaker tile. Do not infer identity from "
            "a face, voice, avatar, appearance, or prior knowledge. A name elsewhere in the participant "
            "grid is not enough. Return JSON only: "
            '{"name": "exact visible text or null", "confidence": 0.0, "active_label_visible": false}. '
            "Set name to null unless the active-speaker relationship and text are both clearly visible."
        )
        response = self.client.responses.create(
            model=self.vision_model,
            store=False,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url, "detail": "high"},
                ],
            }],
        )
        data = safe_json(getattr(response, "output_text", ""))
        if data.get("active_label_visible") is not True:
            return None, 0.0
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, 0.0
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return name.strip(), confidence

    def infer_names_from_video(
        self, source: Path, segments: list[dict]
    ) -> tuple[dict[str, str], dict[str, dict], list[NameObservation]]:
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            return {}, {}, []
        grouped: dict[str, list[dict]] = defaultdict(list)
        for segment in segments:
            grouped[segment["speaker_key"]].append(segment)
        samples = [(key, segment) for key, items in sorted(grouped.items()) for segment in choose_frame_segments(items)]
        if not samples:
            self._progress(76, "No speaker segments were long enough for reliable frame sampling.")
            return {}, {}, []

        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            self.logger.warning("OpenCV could not open video for visible-name sampling")
            return {}, {}, []
        observations: list[NameObservation] = []
        try:
            for index, (speaker_key, segment) in enumerate(samples):
                self._check_cancelled()
                percent = 56 + round(20 * index / len(samples))
                self._progress(percent, f"Checking visible Meet labels ({index + 1}/{len(samples)})…")
                timestamp = float(segment["start"]) + (float(segment["end"]) - float(segment["start"])) * 0.55
                data_url = self._frame_data_url(capture, timestamp)
                if not data_url:
                    continue
                try:
                    name, confidence = self._read_meet_name(data_url)
                except Exception as exc:  # A failed optional frame must not lose the transcript.
                    self.logger.warning("Visible-name check failed at %.2fs: %s", timestamp, exc)
                    continue
                if name:
                    observations.append(NameObservation(speaker_key, name, confidence, timestamp))
        finally:
            capture.release()
        mapping, audit = reconcile_observations(observations)
        for speaker_key in grouped:
            audit.setdefault(speaker_key, {
                "accepted": False,
                "candidate": None,
                "evidence_count": 0,
                "score": 0.0,
                "runner_up_score": 0.0,
                "reason": "no_reliable_visible_active_speaker_text",
            })
        self._progress(77, f"Accepted {len(mapping)} visible speaker-name mapping(s).")
        return mapping, audit, observations

    def _response_text(self, instructions: str, input_text: str) -> str:
        response = self.client.responses.create(
            model=self.summary_model,
            instructions=instructions,
            input=input_text,
            store=False,
        )
        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            raise ProcessingError("The summary model returned an empty response.")
        return text

    def summarize(self, transcript: str) -> str:
        final_instructions = """Create reliable minutes from a primarily Russian-language work call.
Write in English, while preserving names and uncertain organization-specific terms exactly as spoken.
Use only facts stated in the supplied transcript or evidence notes. Never invent or infer a person,
speaker identity, owner, deadline, date, decision, figure, or commitment. Write "Not stated" or
"Unclear from the recording" when evidence is missing. Anonymous labels such as C01-A must remain anonymous.

Use exactly these sections:
# Call Summary
## Executive summary
## Main discussion points
## Decisions
## Action items
A Markdown table with columns: Action | Owner | Deadline | Evidence / context.
## Unresolved questions
## Risks / issues
## Important names, dates, figures and references
## Attribution notes
Explicitly flag every action with an unstated owner or deadline.
"""
        batches = split_text_lines(transcript, SUMMARY_BATCH_CHARS)
        if len(batches) == 1:
            self._check_cancelled()
            self._progress(82, "Creating the English call summary…")
            return self._response_text(final_instructions, transcript)

        evidence_notes: list[str] = []
        extraction_instructions = """Extract factual meeting evidence from this transcript portion.
Preserve timestamps and speaker labels. List discussion points, explicit decisions, explicit actions,
explicit owners, explicit deadlines, open questions, risks, and important names/dates/figures.
Do not infer missing information. Write in concise English; retain exact Russian terms when uncertain."""
        for index, batch in enumerate(batches):
            self._check_cancelled()
            percent = 79 + round(12 * index / len(batches))
            self._progress(percent, f"Extracting summary evidence ({index + 1}/{len(batches)})…")
            evidence_notes.append(self._response_text(extraction_instructions, batch))
        self._progress(93, "Consolidating the final English summary…")
        return self._response_text(final_instructions, "\n\n--- EVIDENCE PART ---\n\n".join(evidence_notes))

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def process(self, source: Path, output_dir: Path, use_video_names: bool) -> ProcessResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info("Processing started: %s", source.name)
        with tempfile.TemporaryDirectory(prefix="flexthatcall_") as temporary:
            segments = self.transcribe(source, Path(temporary))
            names: dict[str, str] = {}
            audit: dict[str, dict] = {}
            observations: list[NameObservation] = []
            if use_video_names and source.suffix.lower() in VIDEO_EXTENSIONS:
                names, audit, observations = self.infer_names_from_video(source, segments)
            else:
                self._progress(77, "Visible speaker-name reading skipped.")
            transcript = render_transcript(segments, names)
            summary = self.summarize(transcript)
            self._check_cancelled()

        stem = source.stem
        transcript_path = output_dir / f"{stem}_transcript.txt"
        json_path = output_dir / f"{stem}_transcript.json"
        summary_path = output_dir / f"{stem}_summary.md"
        payload = {
            "schema_version": 1,
            "source_file": source.name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "models": {
                "transcription": TRANSCRIBE_MODEL,
                "visible_name_reader": self.vision_model if use_video_names else None,
                "summary": self.summary_model,
            },
            "speaker_names": names,
            "speaker_reconciliation": audit,
            "visible_name_observations": [asdict(item) for item in observations],
            "segments": segments,
        }
        self._progress(97, "Writing output files…")
        self._write_text_atomic(transcript_path, transcript)
        self._write_text_atomic(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
        self._write_text_atomic(summary_path, summary)
        self._progress(100, "Finished successfully.")
        self.logger.info("Processing finished: %s", output_dir)
        return ProcessResult(transcript_path, summary_path, json_path)
