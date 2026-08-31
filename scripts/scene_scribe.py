from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scene_scribe_core import (
    bilibili_metadata,
    bilibili_scene_times,
    bilibili_subtitles,
    build_evidence,
    download_media,
    extract_bvid,
    extract_frames,
    file_sha256,
    find_matches,
    format_timestamp,
    load_transcript,
    local_metadata,
    parse_subtitle,
    read_json,
    relative_or_absolute,
    select_keyframe_times,
    transcribe_media,
    write_json,
    write_transcript,
)


def _save_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    write_json(run_dir / "manifest.json", manifest)


def ingest(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    is_local = source_path.exists()
    if is_local:
        metadata = local_metadata(source_path)
    elif extract_bvid(args.source):
        metadata = bilibili_metadata(args.source)
    else:
        raise SystemExit("V0 supports Bilibili URLs and local media files")

    run_dir = Path(args.output).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    previous_manifest_path = run_dir / "manifest.json"
    previous = read_json(previous_manifest_path) if previous_manifest_path.exists() else {}
    write_json(run_dir / "metadata.json", metadata)
    manifest: dict[str, Any] = {
        "version": 1,
        "status": "running",
        "source": metadata,
        "stages": {"metadata": "complete", "transcript": "pending", "frames": "pending"},
        "artifacts": {"metadata": "metadata.json"},
    }
    _save_manifest(run_dir, manifest)

    transcript_path = run_dir / "transcript.jsonl"
    transcript_source = ""
    whisper_info: dict[str, Any] | None = None
    media_dir = run_dir / "media"
    audio_path: Path | None = None
    video_path: Path | None = source_path.resolve() if is_local else None

    if not is_local:
        previous_artifacts = previous.get("artifacts") or {}
        previous_video = previous_artifacts.get("video")
        if previous_video:
            candidate = Path(previous_video)
            video_path = candidate if candidate.is_absolute() else run_dir / candidate
            if not video_path.exists():
                video_path = None
        if video_path is None:
            video_path = next(
                (path for path in sorted(media_dir.glob("video.*")) if path.is_file()), None
            )
        previous_audio = previous_artifacts.get("audio")
        if previous_audio:
            candidate = Path(previous_audio)
            audio_path = candidate if candidate.is_absolute() else run_dir / candidate
            if not audio_path.exists():
                audio_path = None
        if audio_path is None:
            audio_path = next(
                (path for path in sorted(media_dir.glob("audio.*")) if path.is_file()), None
            )

    if transcript_path.exists() and transcript_path.stat().st_size > 0 and not args.force:
        segments = load_transcript(run_dir)
        transcript_source = ((previous.get("transcript") or {}).get("source")) or "cached"
    elif args.subtitle:
        segments = parse_subtitle(Path(args.subtitle))
        transcript_source = "local-subtitle"
        write_transcript(run_dir, segments)
    else:
        subtitle_result = None
        if metadata["platform"] == "bilibili" and not args.force_whisper:
            try:
                subtitle_result = bilibili_subtitles(metadata)
            except Exception as exc:
                manifest.setdefault("warnings", []).append(f"Subtitle API failed: {exc}")
        if subtitle_result:
            segments, transcript_source = subtitle_result
            write_transcript(run_dir, segments)
        else:
            audio_path = (
                source_path.resolve()
                if is_local
                else audio_path or download_media(args.source, media_dir, "audio")
            )
            segments, whisper_info = transcribe_media(
                audio_path,
                model_name=args.whisper_model,
                language=args.language or None,
                device=args.device,
            )
            transcript_source = "faster-whisper"
            write_transcript(run_dir, segments)

    manifest["stages"]["transcript"] = "complete"
    manifest["artifacts"].update(
        {"transcript_jsonl": "transcript.jsonl", "transcript_markdown": "transcript.md"}
    )
    manifest["transcript"] = {
        "source": transcript_source,
        "segments": len(segments),
        "duration": max((segment.end for segment in segments), default=0),
        "whisper": whisper_info,
    }
    if audio_path and audio_path.exists():
        manifest["artifacts"]["audio"] = relative_or_absolute(audio_path, run_dir)
    _save_manifest(run_dir, manifest)

    frames: list[dict[str, Any]] = []
    if args.frames != "none":
        if video_path is None:
            video_path = download_media(args.source, media_dir, "video", max_height=args.max_height)
        scene_times: list[float] = []
        if metadata["platform"] == "bilibili":
            try:
                scene_times = bilibili_scene_times(metadata)
            except Exception as exc:
                manifest.setdefault("warnings", []).append(f"Bilibili scene index failed: {exc}")
        duration = float(metadata.get("duration") or max((segment.end for segment in segments), default=0))
        selections = select_keyframe_times(
            segments,
            scene_times,
            duration,
            max_frames=args.max_frames,
            min_spacing=args.min_frame_spacing,
        )
        frames = extract_frames(video_path, run_dir / "frames", selections)
        manifest["stages"]["frames"] = "complete"
        manifest["artifacts"]["frames"] = "frames/frames.json"
        manifest["artifacts"]["video"] = relative_or_absolute(video_path, run_dir)
    else:
        manifest["stages"]["frames"] = "skipped"

    build_evidence(run_dir, metadata, transcript_source, frames)
    manifest["artifacts"]["evidence"] = "evidence.json"
    manifest["status"] = "complete"
    _save_manifest(run_dir, manifest)
    print(json.dumps({"run_dir": str(run_dir), "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


def locate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    matches = find_matches(load_transcript(run_dir), args.query, context_segments=args.context)
    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))
    else:
        for match in matches:
            print(
                f"[{format_timestamp(match['start'])}-{format_timestamp(match['end'])}] "
                f"match {format_timestamp(match['match_start'])}: {match['text']}"
            )
    return 0 if matches else 1


def frame_at(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    video_value = (manifest.get("artifacts") or {}).get("video")
    if not video_value:
        raise SystemExit("This run has no downloaded video; rerun ingest with --frames auto")
    video_path = Path(video_value)
    if not video_path.is_absolute():
        video_path = run_dir / video_path
    frames = extract_frames(
        video_path,
        run_dir / "frames",
        [{"timestamp": args.seconds, "reason": "requested"}],
        dedupe_distance=-1,
        write_index=False,
    )
    if not frames:
        raise SystemExit("Failed to extract frame")
    evidence = read_json(run_dir / "evidence.json")
    existing = evidence.get("frames") or []
    known = {item["path"] for item in existing}
    evidence["frames"] = existing + [item for item in frames if item["path"] not in known]
    write_json(run_dir / "evidence.json", evidence)
    print(json.dumps(frames[0], ensure_ascii=False, indent=2))
    return 0


def refresh_frames(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    metadata = read_json(run_dir / "metadata.json")
    segments = load_transcript(run_dir)
    video_value = (manifest.get("artifacts") or {}).get("video")
    if not video_value:
        raise SystemExit("This run has no video artifact; rerun ingest with --frames auto")
    video_path = Path(video_value)
    if not video_path.is_absolute():
        video_path = run_dir / video_path
    if not video_path.exists():
        raise SystemExit(f"Video artifact does not exist: {video_path}")

    scene_times: list[float] = []
    if metadata.get("platform") == "bilibili":
        try:
            scene_times = bilibili_scene_times(metadata)
        except Exception as exc:
            manifest.setdefault("warnings", []).append(f"Bilibili scene index failed: {exc}")
    duration = float(metadata.get("duration") or max((item.end for item in segments), default=0))
    selections = select_keyframe_times(
        segments,
        scene_times,
        duration,
        max_frames=args.max_frames,
        min_spacing=args.min_frame_spacing,
    )
    frames_dir = run_dir / "frames"
    # Only generated frame files inside this exact run directory are replaced.
    for generated in frames_dir.glob("frame_*.jpg"):
        generated.unlink()
    frames = extract_frames(video_path, frames_dir, selections)
    transcript_source = ((manifest.get("transcript") or {}).get("source")) or "unknown"
    build_evidence(run_dir, metadata, transcript_source, frames)
    manifest.setdefault("stages", {})["frames"] = "complete"
    manifest.setdefault("artifacts", {})["frames"] = "frames/frames.json"
    manifest["status"] = "complete"
    _save_manifest(run_dir, manifest)
    print(json.dumps({"frames": frames, "count": len(frames)}, ensure_ascii=False, indent=2))
    return 0


def inspect(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    evidence = read_json(run_dir / "evidence.json")
    transcript = run_dir / "transcript.jsonl"
    report = {
        "status": manifest.get("status"),
        "title": (manifest.get("source") or {}).get("title"),
        "transcript_source": (manifest.get("transcript") or {}).get("source"),
        "segments": (manifest.get("transcript") or {}).get("segments"),
        "frames": len(evidence.get("frames") or []),
        "transcript_sha256": file_sha256(transcript) if transcript.exists() else None,
        "warnings": manifest.get("warnings") or [],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="视频总结 Skill: turn media into timestamped evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="create an evidence bundle")
    ingest_parser.add_argument("source", help="Bilibili URL/BVID or local media path")
    ingest_parser.add_argument("--output", required=True, help="run directory")
    ingest_parser.add_argument("--subtitle", help="trusted local SRT/VTT/JSON/JSONL")
    ingest_parser.add_argument("--frames", choices=("none", "auto"), default="none")
    ingest_parser.add_argument("--max-frames", type=int, default=16)
    ingest_parser.add_argument("--min-frame-spacing", type=float, default=12.0)
    ingest_parser.add_argument("--max-height", type=int, default=720)
    ingest_parser.add_argument("--whisper-model", default="small")
    ingest_parser.add_argument("--language", default="zh")
    ingest_parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    ingest_parser.add_argument("--force", action="store_true", help="rebuild cached transcript")
    ingest_parser.add_argument("--force-whisper", action="store_true")
    ingest_parser.set_defaults(func=ingest)

    locate_parser = subparsers.add_parser("locate", help="find a phrase in a run")
    locate_parser.add_argument("run_dir")
    locate_parser.add_argument("query")
    locate_parser.add_argument("--context", type=int, default=2)
    locate_parser.add_argument("--json", action="store_true")
    locate_parser.set_defaults(func=locate)

    frame_parser = subparsers.add_parser("frame-at", help="extract a frame at exact seconds")
    frame_parser.add_argument("run_dir")
    frame_parser.add_argument("seconds", type=float)
    frame_parser.set_defaults(func=frame_at)

    refresh_parser = subparsers.add_parser(
        "refresh-frames", help="reselect frames from an existing evidence bundle"
    )
    refresh_parser.add_argument("run_dir")
    refresh_parser.add_argument("--max-frames", type=int, default=16)
    refresh_parser.add_argument("--min-frame-spacing", type=float, default=12.0)
    refresh_parser.set_defaults(func=refresh_frames)

    inspect_parser = subparsers.add_parser("inspect", help="show bundle status")
    inspect_parser.add_argument("run_dir")
    inspect_parser.set_defaults(func=inspect)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
