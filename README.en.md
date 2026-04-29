# Training Log Extractor (Mandarin Weightlifting Coaching)

This project converts coaching videos into structured training notes.

It does two main things:
- `extract_training_log.py`: transcribes videos and generates transcript + summary + term review files.
- `training_log_web.py`: serves a local web UI to review/edit summary bullets and movement tags.

---

## What This Project Produces

For each video, the extractor can generate:
- `*.transcript.txt`: corrected transcript (dictionary + optional AI corrections).
- `*.transcript.raw.txt`: original transcript backup (written when corrections are applied).
- `*.summary.md`: 3-section coaching summary:
  - `## 关键问题`
  - `## 改进建议`
  - `## 下次训练检查点`
- `*.term_review.md`: deterministic dictionary fixes + AI candidate term fixes.

Global files:
- `training_log_output/training_log.md`
- `training_log_output/training_log.json`

---

## Requirements

- Python 3.10+ (recommended: 3.11+)
- `ffmpeg` installed and available in `PATH`
- Python package: `faster-whisper`
- Required for summary/term review:
  - [Ollama](https://ollama.com/)
  - model such as `qwen2.5:7b-instruct`

### Install dependencies

```bash
pip install faster-whisper
```

Install `ffmpeg` (example on macOS with Homebrew):

```bash
brew install ffmpeg
```

Install and pull Ollama model:

```bash
# Install Ollama (macOS example)
brew install --cask ollama
```

```bash
ollama pull qwen2.5:7b-instruct
```

---

## Folder Layout

Default expected layout:

```text
project-root/
  videos/                     # put all MP4 files here
  training_log_output/        # generated output
  extract_training_log.py
  training_log_web.py
  webui/
```

> `extract_training_log.py` now defaults to `--input-dir videos`.

---

## Quick Start

### 1) Generate logs from videos

From project root:

```bash
python3 extract_training_log.py --auto-correct-transcript
```

This will scan `./videos` by default and write results to `./training_log_output`.

### 2) Launch local review web UI

```bash
python3 training_log_web.py
```

Open in browser:

```text
http://127.0.0.1:8000/webui/
```

---

## Extractor CLI (`extract_training_log.py`)

Common options:

- `--input-dir` (default: `videos`)
- `--output-dir` (default: `training_log_output`)
- `--glob` / `--glob2` (default: `*.MP4`, `*.mp4`)
- `--model-size` (default: `small`)
- `--device` (default: `auto`)
- `--compute-type` (default: `int8`)
- `--disable-summary`
- `--disable-term-review`
- `--auto-correct-transcript`
- `--min-correction-confidence` (default: `0.75`)
- `--disable-dictionary-corrections`

Show full help:

```bash
python3 extract_training_log.py --help
```

---

## Web UI Features (`training_log_web.py`)

- Video selector
- Summary split into 3 panels:
  - `关键问题`
  - `改进建议`
  - `下次训练检查点`
- Per bullet:
  - Movement dropdown (saved back to `*.summary.md`)
  - Editable text + **Update** button
  - **Delete** button (removes bullet and updates `*.summary.md`)
- Full transcript panel

Server options:

- `--host` (default: `127.0.0.1`)
- `--port` (default: `8000`)
- `--root` (default: current directory)
- `--log-dir` (default: `training_log_output`)
- `--auto-stop-seconds` (default: `20`, set `0` to disable auto-stop)

Show full help:

```bash
python3 training_log_web.py --help
```

---

## Typical Workflow

1. Put videos in `videos/`.
2. Run extractor:
   - `python3 extract_training_log.py --auto-correct-transcript`
3. Open web UI:
   - `python3 training_log_web.py`
4. Review summary bullets:
   - assign movement tags
   - edit bullet text
   - delete wrong bullets
5. Re-run extractor for new videos as needed.

---

## Troubleshooting

### `ffmpeg is required but not found`
Install `ffmpeg` and verify:

```bash
ffmpeg -version
```

### `Missing dependency faster-whisper`

```bash
pip install faster-whisper
```

### Summary / term review generation fails

- Ensure Ollama is installed and running.
- Ensure model exists:

```bash
ollama list
```

- If needed, disable LLM steps:

```bash
python3 extract_training_log.py --disable-summary --disable-term-review
```

### No videos found

- Ensure files are in `videos/`
- Check extension case and globs (`*.MP4`, `*.mp4`)
- Or pass a custom input dir:

```bash
python3 extract_training_log.py --input-dir /path/to/videos
```

---

## Notes

- The project is optimized for Mandarin weightlifting coaching language.
- Deterministic dictionary corrections are enabled by default.
- AI candidate term corrections are listed for human review; auto-apply depends on confidence threshold.
