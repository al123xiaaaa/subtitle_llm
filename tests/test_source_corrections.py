import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from subtitle_llm.domain import SubtitleEntry
from subtitle_llm.pipeline.source_corrections import (
    SourceCorrection,
    find_unadopted_hard_corrections,
)


class TestSourceCorrections(unittest.TestCase):
    def test_hard_named_entity_correction_is_remapped_by_observed_text(self):
        entries = [
            SubtitleEntry(
                30,
                "00:00:30,000",
                "00:00:31,000",
                "so cheap side.The main market street of Tudor London",
                "这边便宜。都铎伦敦的主要市场大街。",
            )
        ]
        correction = SourceCorrection(
            cue_ids=(29,),
            observed="cheap side",
            corrected="Cheapside",
            correction_type="street",
            enforcement="hard",
            target_aliases=("齐普赛街", "Cheapside"),
            confidence="high",
            evidence="The source describes the main market street.",
        )

        flags = find_unadopted_hard_corrections([correction], entries)

        self.assertEqual([flag.cue_id for flag in flags], [30])
        self.assertEqual(flags[0].target_aliases, ("齐普赛街", "Cheapside"))

    def test_alias_core_accepts_reasonable_chinese_name_variant(self):
        entries = [
            SubtitleEntry(
                12,
                "00:00:12,000",
                "00:00:13,000",
                "Boar's Head Inn",
                "野猪头客栈。",
            )
        ]
        correction = SourceCorrection(
            cue_ids=(12,),
            observed="Boar's Head",
            corrected="Boar's Head Inn",
            correction_type="inn",
            enforcement="hard",
            target_aliases=("野猪头旅馆",),
            confidence="high",
        )

        flags = find_unadopted_hard_corrections([correction], entries)

        self.assertEqual(flags, [])

    def test_hard_gate_ignores_non_named_entity_corrections(self):
        entries = [
            SubtitleEntry(
                7,
                "00:00:07,000",
                "00:00:08,000",
                "a doublet",
                "一件上衣。",
            )
        ]
        correction = SourceCorrection(
            cue_ids=(7,),
            observed="doublet",
            corrected="doublet",
            correction_type="common_term",
            enforcement="hard",
            target_aliases=("紧身上衣",),
            confidence="high",
        )

        flags = find_unadopted_hard_corrections([correction], entries)

        self.assertEqual(flags, [])


if __name__ == "__main__":
    unittest.main()
