---
name: scene-scribe
description: Read long-form videos and local media as traceable evidence by recovering timestamped speech, selecting meaningful frames, and producing source-grounded summaries. Use for video summaries, lecture notes, visual walkthroughs, or follow-up questions that need exact timestamps or screenshots; do not use for video editing or generation.
---

# 镜读 SceneScribe

Turn a video into a reusable evidence bundle before summarizing it. Keep source recovery deterministic and let the active agent perform interpretation.

## Read modes

- **Quick read:** transcript, outline, key claims, and exact timestamps. Do not download video frames unless the spoken record is insufficient.
- **Visual read:** transcript plus scene-aware frames. Use when slides, code, demonstrations, diagrams, or on-screen text materially carry the argument.
- **Source check:** locate a phrase or claim in an existing run, inspect nearby transcript segments, and extract a frame at that exact time when useful.

For detailed routing and reporting rules, read [references/reading-modes.md](references/reading-modes.md). Read [references/output-schema.md](references/output-schema.md) when consuming or extending generated artifacts.

## Workflow

1. Create an evidence bundle with `scripts/scene_scribe.py ingest`. Prefer platform subtitles; use local Whisper only when usable subtitles are absent or explicitly rejected.
2. Treat `transcript.jsonl` as the canonical speech timeline. Preserve its timestamps and record whether text came from human captions, automatic captions, or ASR.
3. For visual reads, select frames from scene changes, transcript visual cues, and timeline coverage. Deduplicate similar frames and keep the selection small.
4. Read `evidence.json`, then load only the transcript ranges and frames relevant to the user's request.
5. Separate sourced statements from interpretation. Give clickable source timestamps when the platform supports them, and disclose ASR uncertainty for exact quotations or names.

Typical commands:

```powershell
python scripts/scene_scribe.py ingest "<video-url-or-file>" --output "<run-dir>" --frames auto
python scripts/scene_scribe.py locate "<run-dir>" "search phrase"
python scripts/scene_scribe.py frame-at "<run-dir>" 4937
python scripts/scene_scribe.py refresh-frames "<run-dir>" --max-frames 18
python scripts/scene_scribe.py inspect "<run-dir>"
```

Use `--frames none` for a quick read. Use `--subtitle <file>` for a trusted local SRT, VTT, JSON, or JSONL transcript. Reuse a completed run instead of downloading or transcribing the same source again.
Use `refresh-frames` after changing frame-selection policy; it reuses the cached transcript and video and replaces only the generated frames for that run.

## Constraints

- Never invent timestamps, quotations, visual details, or speaker identities.
- Do not treat ASR text as verbatim when accuracy matters; inspect the audio context or phrase the result as a transcription.
- Do not persist cookies, API keys, or authenticated media in the skill directory. Ask before using private-session credentials.
- Keep downloaded full media in the requested run directory or a temporary directory. Do not commit generated media or transcripts unless the user explicitly requests it.
- If media acquisition fails, retain any valid metadata or transcript already recovered and report the failed stage.
