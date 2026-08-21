# FlexThatCall

FlexThatCall is a simple Windows desktop application that turns primarily Russian-language Google Meet recordings into an auditable diarized transcript and an English meeting summary.

It creates:

- `*_transcript.txt` — readable speaker-segment transcript with timestamps;
- `*_transcript.json` — raw segments, models, visible-name evidence, and reconciliation decisions;
- `*_summary.md` — executive summary, discussion points, decisions, action items, owners, deadlines, unresolved questions, risks, and important references.

FlexThatCall never identifies people from faces or voices. For a Meet video it can read only a participant name visibly attached to the active-speaker UI. When that evidence is missing, inconsistent, or ambiguous, labels such as `C01-A` remain anonymous.

## Processing pipeline

1. Audio is extracted locally with the ffmpeg executable supplied by `imageio-ffmpeg`.
2. Long recordings are converted to mono 16 kHz MP3 and split into 20-minute chunks.
3. Each chunk is transcribed in Russian with `gpt-4o-transcribe-diarize` using `diarized_json` and server-side automatic chunking.
4. Speaker timestamps are shifted back onto the full-recording timeline. Chunk-local labels are namespaced (`C01-A`, `C02-A`) because speaker A in two independent chunks is not necessarily the same person.
5. For video only, several time-diverse frames are sampled for each diarized speaker. A vision-capable model is asked to read visible Google Meet active-speaker text only.
6. A name is accepted only after consistent visible-text observations, or one exceptionally confident unambiguous observation. The JSON output retains the evidence and acceptance reason.
7. The transcript is summarized in English. Long transcripts use a fact-extraction pass followed by consolidation.

The transcription request follows the current [OpenAI audio transcription API](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create). The diarization model is documented in the [OpenAI model catalog](https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize).

## Windows installation

### What you need

- Windows 10 or 11;
- 64-bit Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/);
- an OpenAI API key with API billing and access to the configured models;
- internet access while installing packages and processing calls.

During Python installation, enable **Add python.exe to PATH**. FlexThatCall supplies ffmpeg through a Python package, so a separate ffmpeg installation is normally unnecessary.

### Install from PowerShell

Open PowerShell in the repository folder, then run:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The execution-policy command affects only the current PowerShell window. If `python` is not recognized, reinstall Python with the PATH option enabled, or use the full path to `python.exe`.

### Start the application

With the virtual environment active:

```powershell
python app.py
```

No command-line arguments are required.

## First use

1. Select or drag one `.mp4` recording (audio formats are also supported).
2. Confirm the output folder. By default it is `FlexThatCall_Output` beside the recording.
3. Paste an OpenAI API key.
4. Leave **Remember securely in Windows Credential Manager** enabled if desired.
5. For a Google Meet video, leave visible active-speaker name reading enabled.
6. Select **Transcribe & summarize**.

The progress bar and processing log show audio preparation, every transcription chunk, every sampled Meet frame, summary passes, and output writing. **Cancel** takes effect after the current local/API operation completes. No partial result files are written.

## API key and configuration security

The API key is handled in this order:

1. `OPENAI_API_KEY`, if set in the process environment;
2. Windows Credential Manager through Python `keyring`;
3. the key pasted into the GUI for the current session.

The key is never written to the repository, JSON settings, output files, or application log. **Forget saved key** removes the Credential Manager entry.

Non-secret settings are stored at:

```text
%APPDATA%\FlexThatCall\settings.json
```

They include the last source/output locations, visible-name preference, remember-key preference, and model names. To reset these preferences, close the app and delete that file.

Optional environment overrides:

```powershell
$env:FLEXTHATCALL_SUMMARY_MODEL = "gpt-5-mini"
$env:FLEXTHATCALL_VISION_MODEL = "gpt-5-mini"
python app.py
```

## Logs

Rotating logs are stored at:

```text
%LOCALAPPDATA%\FlexThatCall\logs\flexthatcall.log
```

Use **Open log folder** in the application. The current log and up to three rotated files are retained. Logs contain processing stages and technical errors, but not the API key or transcript content.

## Output details

Example transcript:

```text
[00:03:14–00:03:22] Иван Петров: Давайте тогда перенесём это на пятницу.
[00:03:23–00:03:29] C01-B: Да, я подготовлю таблицу до обеда.
```

The JSON file includes:

- full-recording segment start/end times;
- raw and namespaced diarization labels;
- confidently accepted visible names;
- visible-name observations with timestamps and confidence;
- reconciliation audit entries, including rejected ambiguous candidates;
- models used and generation time.

The summary instructs the model to use `Not stated` or `Unclear from the recording` instead of inventing an owner, deadline, identity, decision, date, figure, or commitment. Machine-generated minutes should still be reviewed before they become an official record.

## Troubleshooting

### `python` is not recognized

Reinstall Python and select **Add Python to PATH**, then open a new PowerShell window.

### PowerShell will not activate `.venv`

Run this once in the same PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### API key rejected or model permission denied

Confirm that the key is an API key (not a ChatGPT login), billing is enabled for its API project, and the project can access `gpt-4o-transcribe-diarize` plus the configured summary/vision models.

### No visible participant names are recovered

This is expected if the Meet recording did not capture the active-speaker label, text is too small, the layout changed, or readings disagree. Anonymous labels are safer than a guessed identity.

### Audio preparation fails

Check that the recording opens in a media player and has an audio track. The application log contains ffmpeg's error message.

## Development and tests

Install development requirements and run the non-API test suite:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover time formatting, tolerant JSON parsing, transcript rendering, line-safe summary batching, file validation, configuration persistence without secret fields, time-diverse frame selection, name normalization, consensus acceptance, and ambiguity rejection. They do not call the OpenAI API.

## Privacy and consent

Audio chunks, sampled video frames, and transcript text are sent to the OpenAI API. Audio extraction and frame sampling happen locally. Process recordings only when organizational policy, participant consent requirements, and applicable law permit it.

## License

See [LICENSE](LICENSE).
