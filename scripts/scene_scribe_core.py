from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}", re.IGNORECASE)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


def format_timestamp(seconds: float, *, include_hours: bool | None = None) -> str:
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if include_hours is None:
        include_hours = hours > 0
    if include_hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours * 60 + minutes:02d}:{secs:02d}"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_bytes(url: str, *, referer: str | None = None, timeout: int = 30) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str, *, referer: str | None = None, timeout: int = 30) -> Any:
    return json.loads(fetch_bytes(url, referer=referer, timeout=timeout).decode("utf-8"))


def extract_bvid(source: str) -> str | None:
    match = BVID_RE.search(source)
    return match.group(0) if match else None


def bilibili_metadata(source: str) -> dict[str, Any]:
    bvid = extract_bvid(source)
    if not bvid:
        raise ValueError("No Bilibili BV identifier found")
    payload = fetch_json(
        "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bvid}),
        referer="https://www.bilibili.com/",
    )
    if payload.get("code") != 0:
        raise RuntimeError(f"Bilibili metadata API failed: {payload.get('message')}")
    data = payload["data"]
    page = data.get("pages", [{}])[0]
    return {
        "platform": "bilibili",
        "id": data.get("bvid", bvid),
        "aid": data.get("aid"),
        "cid": data.get("cid") or page.get("cid"),
        "title": data.get("title") or page.get("part") or bvid,
        "author": (data.get("owner") or {}).get("name"),
        "duration": float(data.get("duration") or page.get("duration") or 0),
        "description": data.get("desc") or "",
        "source_url": f"https://www.bilibili.com/video/{bvid}/",
    }


def bilibili_subtitles(metadata: dict[str, Any]) -> tuple[list[Segment], str] | None:
    bvid = metadata["id"]
    cid = metadata.get("cid")
    if not cid:
        return None
    endpoint = "https://api.bilibili.com/x/player/v2?" + urllib.parse.urlencode(
        {"bvid": bvid, "cid": cid}
    )
    payload = fetch_json(endpoint, referer=metadata["source_url"])
    entries = ((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not entries:
        return None
    human = [entry for entry in entries if not entry.get("ai_type")]
    entry = (human or entries)[0]
    subtitle_url = entry.get("subtitle_url") or entry.get("url")
    if not subtitle_url:
        return None
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    body = fetch_json(subtitle_url, referer=metadata["source_url"])
    segments = [
        Segment(float(item["from"]), float(item["to"]), str(item["content"]).strip())
        for item in body.get("body", [])
        if str(item.get("content", "")).strip()
    ]
    if not segments:
        return None
    source = "human-subtitle" if human else "automatic-subtitle"
    return normalize_segments(segments), source


def bilibili_scene_times(metadata: dict[str, Any]) -> list[float]:
    params = {"bvid": metadata["id"], "index": 1}
    if metadata.get("cid"):
        params["cid"] = metadata["cid"]
    payload = fetch_json(
        "https://api.bilibili.com/x/player/videoshot?" + urllib.parse.urlencode(params),
        referer=metadata["source_url"],
    )
    if payload.get("code") != 0:
        return []
    values = (payload.get("data") or {}).get("index") or []
    return sorted({float(value) for value in values if float(value) >= 0})


def local_metadata(source: Path) -> dict[str, Any]:
    try:
        import av

        with av.open(str(source)) as container:
            duration = float(container.duration or 0) / float(av.time_base)
    except Exception:
        duration = 0.0
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:12]
    return {
        "platform": "local",
        "id": digest,
        "title": source.stem,
        "author": None,
        "duration": duration,
        "description": "",
        "source_url": str(source.resolve()),
    }


def normalize_segments(segments: Iterable[Segment]) -> list[Segment]:
    clean: list[Segment] = []
    for segment in sorted(segments, key=lambda value: (value.start, value.end)):
        text = re.sub(r"\s+", " ", segment.text).strip()
        if not text:
            continue
        start = max(0.0, float(segment.start))
        end = max(start, float(segment.end))
        if clean and start < clean[-1].end:
            start = clean[-1].end
            end = max(start, end)
        clean.append(Segment(start, end, text))
    return clean


def _clock_to_seconds(value: str) -> float:
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = "0", parts[0], parts[1]
    else:
        raise ValueError(f"Unsupported subtitle timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_subtitle(path: Path) -> list[Segment]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    if suffix == ".jsonl":
        return normalize_segments(
            Segment(float(item["start"]), float(item["end"]), str(item["text"]))
            for item in (json.loads(line) for line in text.splitlines() if line.strip())
        )
    if suffix == ".json":
        value = json.loads(text)
        rows = value.get("body", value) if isinstance(value, dict) else value
        return normalize_segments(
            Segment(
                float(item.get("start", item.get("from", 0))),
                float(item.get("end", item.get("to", item.get("start", 0)))),
                str(item.get("text", item.get("content", ""))),
            )
            for item in rows
        )
    if suffix not in {".srt", ".vtt"}:
        raise ValueError(f"Unsupported subtitle format: {suffix}")
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("WEBVTT", "", 1).strip())
    result: list[Segment] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        left, right = [part.strip().split(" ", 1)[0] for part in lines[timing_index].split("-->", 1)]
        caption = re.sub(r"<[^>]+>", "", " ".join(lines[timing_index + 1 :])).strip()
        if caption:
            result.append(Segment(_clock_to_seconds(left), _clock_to_seconds(right), caption))
    return normalize_segments(result)


def write_transcript(run_dir: Path, segments: Sequence[Segment]) -> None:
    jsonl = run_dir / "transcript.jsonl"
    with jsonl.open("w", encoding="utf-8", newline="\n") as handle:
        for segment in segments:
            handle.write(json.dumps(segment.as_dict(), ensure_ascii=False) + "\n")
    lines = ["# Transcript", ""]
    for segment in segments:
        lines.append(f"**{format_timestamp(segment.start)}** {segment.text}")
        lines.append("")
    (run_dir / "transcript.md").write_text("\n".join(lines), encoding="utf-8")


def load_transcript(run_dir: Path) -> list[Segment]:
    return parse_subtitle(run_dir / "transcript.jsonl")


def download_media(source: str, media_dir: Path, kind: str, *, max_height: int = 720) -> Path:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required to download remote media") from exc
    media_dir.mkdir(parents=True, exist_ok=True)
    if kind == "audio":
        selector = "bestaudio[ext=m4a]/bestaudio/best"
        stem = "audio"
    elif kind == "video":
        selector = f"bestvideo[height<={max_height}]/best[height<={max_height}]/best"
        stem = "video"
    else:
        raise ValueError(f"Unknown media kind: {kind}")
    options = {
        "format": selector,
        "outtmpl": str(media_dir / f"{stem}.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "retries": 3,
        "http_headers": {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(source, download=True)
        requested = info.get("requested_downloads") or []
        candidates = [Path(item["filepath"]) for item in requested if item.get("filepath")]
    candidates.extend(sorted(media_dir.glob(f"{stem}.*")))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    raise RuntimeError(f"yt-dlp completed without producing a {kind} file")


def transcribe_media(
    media_path: Path,
    *,
    model_name: str = "small",
    language: str | None = "zh",
    device: str = "auto",
) -> tuple[list[Segment], dict[str, Any]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is required when no subtitle is available") from exc

    attempts: list[tuple[str, str]]
    if device == "auto":
        attempts = [("cuda", "float16"), ("cpu", "int8")]
    elif device == "cuda":
        attempts = [("cuda", "float16")]
    else:
        attempts = [("cpu", "int8")]
    errors: list[str] = []
    for selected_device, compute_type in attempts:
        try:
            model = WhisperModel(model_name, device=selected_device, compute_type=compute_type)
            generated, info = model.transcribe(
                str(media_path),
                language=language,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=True,
            )
            segments = normalize_segments(
                Segment(float(item.start), float(item.end), str(item.text)) for item in generated
            )
            return segments, {
                "model": model_name,
                "device": selected_device,
                "compute_type": compute_type,
                "language": info.language,
                "duration": float(info.duration),
            }
        except Exception as exc:
            errors.append(f"{selected_device}/{compute_type}: {exc}")
    raise RuntimeError("Whisper transcription failed: " + " | ".join(errors))


def get_ffmpeg_executable() -> str:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("ffmpeg or imageio-ffmpeg is required for frame extraction") from exc


VISUAL_CUES = {
    # Generic deictic phrases are weak signals. Concrete visual nouns and
    # named concepts deserve to survive both selection and image deduplication.
    "你看": 1,
    "大家看": 2,
    "可以看到": 2,
    "看一下": 2,
    "左边": 2,
    "右边": 2,
    "代码": 3,
    "界面": 3,
    "幻灯片": 4,
    "slide": 4,
    "这张图": 5,
    "这个图": 5,
    "架构": 6,
    "中间语言": 10,
    "三个空间": 10,
}


def _nearest(values: Sequence[float], target: float) -> float:
    if not values:
        return target
    return min(values, key=lambda value: abs(value - target))


def select_keyframe_times(
    segments: Sequence[Segment],
    scene_times: Sequence[float],
    duration: float,
    *,
    max_frames: int = 16,
    min_spacing: float = 12.0,
) -> list[dict[str, Any]]:
    if max_frames <= 0 or duration <= 0:
        return []
    scenes = sorted({time for time in scene_times if 0 <= time <= duration})
    coverage_count = min(max_frames, max(2, max_frames // 2))
    coverage: list[dict[str, Any]] = []
    for index in range(coverage_count):
        target = duration * (index + 0.5) / coverage_count
        coverage.append(
            {"timestamp": _nearest(scenes, target), "reason": "timeline-coverage", "score": 1}
        )

    cue_hits: list[tuple[float, str, int]] = []
    for segment in segments:
        matched = [cue for cue in VISUAL_CUES if cue.casefold() in segment.text.casefold()]
        if matched:
            cue_hits.append(
                (segment.start, segment.text, max(VISUAL_CUES[cue] for cue in matched))
            )
    buckets: dict[int, tuple[float, str, int]] = {}
    for hit in cue_hits:
        bucket = int(hit[0] // 60)
        current = buckets.get(bucket)
        if current is None or hit[2] > current[2]:
            buckets[bucket] = hit
    cues: list[dict[str, Any]] = []
    for timestamp, text, strength in buckets.values():
        cues.append(
            {
                "timestamp": _nearest(scenes, timestamp),
                "reason": "visual-cue",
                "score": strength,
                "context": text,
            }
        )

    selected: list[dict[str, Any]] = []

    def add(proposal: dict[str, Any]) -> bool:
        if any(abs(proposal["timestamp"] - item["timestamp"]) < min_spacing for item in selected):
            return False
        selected.append(proposal)
        return True

    # Reserve a small number of slots for unusually specific concepts first.
    # This prevents an important late diagram from losing to dozens of early
    # generic phrases such as "你看".
    strong_limit = max(1, max_frames // 4)
    strong_added = 0
    for proposal in sorted(cues, key=lambda item: (-item["score"], item["timestamp"])):
        if proposal["score"] < 8 or strong_added >= strong_limit:
            continue
        if add(proposal):
            strong_added += 1

    # Coverage is a separate quota rather than a low-scoring proposal pool.
    # Therefore the beginning of a long talk cannot consume every slot.
    for proposal in coverage:
        if len(selected) >= max_frames:
            break
        add(proposal)

    # Fill remaining slots with cues, preferring both specificity and temporal
    # distance from what is already selected.
    remaining_cues = [item for item in cues if item not in selected]
    while remaining_cues and len(selected) < max_frames:
        proposal = max(
            remaining_cues,
            key=lambda item: (
                item["score"],
                min(
                    (abs(item["timestamp"] - chosen["timestamp"]) for chosen in selected),
                    default=duration,
                ),
                -item["timestamp"],
            ),
        )
        remaining_cues.remove(proposal)
        add(proposal)

    if len(selected) < max_frames:
        remaining = sorted(
            scenes,
            key=lambda value: min((abs(value - item["timestamp"]) for item in selected), default=duration),
            reverse=True,
        )
        for timestamp in remaining:
            if any(abs(timestamp - item["timestamp"]) < min_spacing for item in selected):
                continue
            selected.append({"timestamp": timestamp, "reason": "scene-change", "score": 2})
            if len(selected) >= max_frames:
                break
    return sorted(selected, key=lambda item: item["timestamp"])


def _image_hash(path: Path) -> int:
    from PIL import Image

    with Image.open(path) as image:
        pixels = list(image.convert("L").resize((9, 8)).getdata())
    value = 0
    for row in range(8):
        for column in range(8):
            value <<= 1
            if pixels[row * 9 + column] > pixels[row * 9 + column + 1]:
                value |= 1
    return value


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def extract_frames(
    video_path: Path,
    frames_dir: Path,
    selections: Sequence[dict[str, Any]],
    *,
    dedupe_distance: int = 5,
    write_index: bool = True,
) -> list[dict[str, Any]]:
    ffmpeg = get_ffmpeg_executable()
    frames_dir.mkdir(parents=True, exist_ok=True)
    accepted: list[dict[str, Any]] = []
    hashes: list[int] = []
    # Extract high-value cues first so perceptual deduplication retains them
    # instead of an earlier but less informative near-duplicate slide.
    ordered = sorted(
        selections,
        key=lambda item: (-int(item.get("score", 0)), float(item["timestamp"])),
    )
    for selection in ordered:
        timestamp = float(selection["timestamp"])
        filename = f"frame_{int(round(timestamp)):06d}.jpg"
        destination = frames_dir / filename
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(destination),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not destination.exists():
            continue
        digest = _image_hash(destination)
        if dedupe_distance >= 0 and any(_hamming(digest, previous) <= dedupe_distance for previous in hashes):
            destination.unlink(missing_ok=True)
            continue
        hashes.append(digest)
        accepted.append(
            {
                "timestamp": round(timestamp, 3),
                "path": str(Path("frames") / filename).replace("\\", "/"),
                "reason": selection.get("reason", "scene-change"),
                "context": selection.get("context", ""),
                "score": selection.get("score", 0),
            }
        )
    accepted.sort(key=lambda item: item["timestamp"])
    if write_index:
        write_json(frames_dir / "frames.json", accepted)
    return accepted


def transcript_context(segments: Sequence[Segment], timestamp: float, radius: float = 8.0) -> str:
    return "".join(
        segment.text
        for segment in segments
        if segment.end >= timestamp - radius and segment.start <= timestamp + radius
    )


def build_evidence(
    run_dir: Path,
    metadata: dict[str, Any],
    transcript_source: str,
    frames: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    segments = load_transcript(run_dir)
    enriched = []
    for frame in frames:
        item = dict(frame)
        if not item.get("context"):
            item["context"] = transcript_context(segments, float(item["timestamp"]))
        enriched.append(item)
    evidence = {
        "version": 1,
        "source": {
            "platform": metadata.get("platform"),
            "id": metadata.get("id"),
            "url": metadata.get("source_url"),
            "title": metadata.get("title"),
        },
        "transcript": {"path": "transcript.jsonl", "source": transcript_source},
        "frames": enriched,
    }
    write_json(run_dir / "evidence.json", evidence)
    return evidence


def find_matches(
    segments: Sequence[Segment], query: str, *, context_segments: int = 2
) -> list[dict[str, Any]]:
    needle = query.casefold()
    hits: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if needle not in segment.text.casefold():
            continue
        left = max(0, index - context_segments)
        right = min(len(segments), index + context_segments + 1)
        context = segments[left:right]
        hits.append(
            {
                "start": context[0].start,
                "end": context[-1].end,
                "match_start": segment.start,
                "match_end": segment.end,
                "text": "".join(item.text for item in context),
            }
        )
    return hits


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())
