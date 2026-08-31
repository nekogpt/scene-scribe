from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from scene_scribe_core import (  # noqa: E402
    Segment,
    extract_bvid,
    find_matches,
    format_timestamp,
    parse_subtitle,
    select_keyframe_times,
    write_transcript,
)


class SceneScribeTests(unittest.TestCase):
    def test_bvid_and_timestamp(self) -> None:
        self.assertEqual(extract_bvid("https://bilibili.com/video/BV1pb8o6yE8f/"), "BV1pb8o6yE8f")
        self.assertEqual(format_timestamp(4937.2), "01:22:17")
        self.assertEqual(format_timestamp(125), "02:05")

    def test_srt_normalization_and_jsonl_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subtitle = root / "sample.srt"
            subtitle.write_text(
                "1\n00:00:01,000 --> 00:00:03,000\nHello  world\n\n"
                "2\n00:00:02,500 --> 00:00:04,000\nSecond\n",
                encoding="utf-8",
            )
            segments = parse_subtitle(subtitle)
            self.assertEqual([item.text for item in segments], ["Hello world", "Second"])
            self.assertEqual(segments[1].start, 3.0)
            write_transcript(root, segments)
            lines = [
                json.loads(line)
                for line in (root / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(lines[0]["start"], 1.0)
            self.assertEqual(lines[1]["end"], 4.0)

    def test_locate_keeps_context(self) -> None:
        segments = [
            Segment(0, 2, "before"),
            Segment(2, 4, "三个空间"),
            Segment(4, 6, "after"),
        ]
        matches = find_matches(segments, "三个", context_segments=1)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["start"], 0)
        self.assertEqual(matches[0]["end"], 6)

    def test_keyframes_mix_coverage_and_visual_cues(self) -> None:
        segments = [
            Segment(0, 3, "intro"),
            Segment(18, 22, "你看这里"),
            Segment(118, 122, "大家看这个架构"),
            Segment(493, 497, "这里有一个中间语言"),
            Segment(518, 522, "我们穿过三个空间"),
            Segment(596, 600, "end"),
        ]
        scenes = list(range(0, 601, 10))
        selected = select_keyframe_times(segments, scenes, 600, max_frames=8, min_spacing=10)
        self.assertLessEqual(len(selected), 8)
        self.assertTrue(any(item["timestamp"] == 120 for item in selected))
        self.assertTrue(any(item["timestamp"] == 490 for item in selected))
        self.assertTrue(any(item["timestamp"] == 520 for item in selected))
        self.assertTrue(any(item["reason"] == "timeline-coverage" for item in selected))
        coverage_times = [item["timestamp"] for item in selected if item["reason"] == "timeline-coverage"]
        self.assertTrue(any(timestamp < 300 for timestamp in coverage_times))
        self.assertTrue(any(timestamp > 300 for timestamp in coverage_times))


if __name__ == "__main__":
    unittest.main()
