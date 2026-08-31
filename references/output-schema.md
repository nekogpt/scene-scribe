# Evidence bundle schema

Each run is self-contained:

```text
run-dir/
|-- manifest.json
|-- metadata.json
|-- transcript.jsonl
|-- transcript.md
|-- evidence.json
|-- media/                 downloaded audio/video when needed
`-- frames/                selected JPEG frames and frames.json
```

## transcript.jsonl

One JSON object per line:

```json
{"start": 5092.75, "end": 5094.19, "text": "我们有三个空间"}
```

`start` and `end` are seconds from the beginning of the selected video part. Segments must be sorted and non-overlapping after normalization.

## manifest.json

Records source identity, processing decisions, artifact paths, transcript provenance, and stage status. `transcript.source` is one of `human-subtitle`, `automatic-subtitle`, `faster-whisper`, or `local-subtitle`.

## evidence.json

The evidence bundle points to artifacts rather than duplicating the whole transcript:

```json
{
  "version": 1,
  "source": {"url": "...", "id": "..."},
  "transcript": {"path": "transcript.jsonl", "source": "faster-whisper"},
  "frames": [
    {
      "timestamp": 4937.2,
      "path": "frames/frame_004937.jpg",
      "reason": "visual-cue",
      "context": "也就是说我做了一个中间语言",
      "score": 10
    }
  ]
}
```

Paths are relative to the run directory so bundles remain movable.
`score` is a deterministic selection priority, not a confidence score. It is used to keep specific conceptual frames ahead of generic visual cues during perceptual deduplication.
