# Reading and reporting modes

## Quick read

Use for ordinary requests to summarize a lecture, interview, podcast, or talk. Ingest with `--frames none`, read the full timeline in manageable time windows, and report:

- one-sentence thesis;
- major claims and supporting examples;
- a compact timestamp guide;
- clearly marked evaluation only when the user asks for it.

Do not imply that a text-only pass covers unspoken screen content.

## Visual read

Use when the video contains slides, code, diagrams, demonstrations, interfaces, or when the user asks for screenshots. Ingest with `--frames auto`. Inspect selected frames before referring to their content. If a critical spoken passage lacks a useful selected frame, run `frame-at` at that timestamp and inspect the result.

Automatic selection reserves separate quotas for timeline coverage and high-specificity visual cues. Run `refresh-frames` when the selection policy changes or an existing bundle is visibly biased toward one part of the video.

Prefer a few informative images over repetitive frame dumps. Captions should say what the image establishes and include its timestamp.

## Source check

Use `locate` before answering where something appears. Return the smallest useful interval, normally including 10-30 seconds of surrounding context. For purported quotations, state that wording comes from ASR unless the source was a trusted subtitle or the audio was independently checked.

## Grounding rules

- A claim is source-grounded only when it maps to a transcript interval, an inspected frame, or both.
- Interpretation is allowed, but label it as interpretation and do not attribute it to the speaker.
- Preserve disagreements, caveats, and deliberately provocative rhetoric rather than flattening them into factual claims.
- When a frame and transcript conflict, report the conflict instead of silently choosing one.
