# 视频总结 Skill

`scene-scribe` turns long-form video into a traceable evidence bundle before an agent summarizes it. It preserves timestamped speech, selects useful frames, and keeps every conclusion traceable to the source.

This repository is currently maintained as a private personal Codex skill.

## What it does

- accepts Bilibili URLs/BVIDs and local media;
- prefers platform subtitles and falls back to `faster-whisper`;
- normalizes speech into timestamped `transcript.jsonl`;
- combines scene changes, timeline coverage, and visual cues for keyframe selection;
- supports exact phrase lookup and frame extraction at requested timestamps;
- writes a portable `evidence.json` bundle for grounded summarization.

## Quick start

```powershell
python scripts/scene_scribe.py ingest "<video-url-or-file>" --output "<run-dir>" --frames auto
python scripts/scene_scribe.py locate "<run-dir>" "search phrase"
python scripts/scene_scribe.py frame-at "<run-dir>" 4937
python scripts/scene_scribe.py inspect "<run-dir>"
```

See [SKILL.md](./SKILL.md) for the agent workflow and constraints. Generated media, transcripts, and frames should remain outside this repository unless intentionally added as a small test fixture.

## Validation

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m unittest discover -s tests -v
```
