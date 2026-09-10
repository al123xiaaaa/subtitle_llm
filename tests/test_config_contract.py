"""desktop-contract.json 与 Python 侧默认值的一致性锁定。

契约文件是桌面端生成 YAML 的单一来源；这里的测试保证 Python 侧
settings.py 的 ASRConfig 默认值与契约不漂移（历史上漂移真实发生过：
桌面端曾静默覆盖 ASR 默认模型为 SenseVoiceSmall）。
"""

from __future__ import annotations

import json
import unittest
from importlib import resources

from subtitle_llm.media.downloader import _common_ffmpeg_paths
from subtitle_llm.settings import ASRConfig


def load_contract() -> dict:
    return json.loads(resources.files("subtitle_llm.config").joinpath("desktop-contract.json").read_text("utf-8"))


class ConfigContractParityTest(unittest.TestCase):
    def test_asr_defaults_match_contract(self) -> None:
        contract_asr = load_contract()["asr"]
        asr = ASRConfig()
        self.assertEqual(asr.model_name, contract_asr["model_name"])
        self.assertEqual(asr.punc_model, contract_asr["punc_model"])
        self.assertEqual(asr.spk_model, contract_asr["spk_model"])
        self.assertEqual(asr.max_single_segment_time, contract_asr["max_single_segment_time"])
        self.assertEqual(asr.device, contract_asr["device"])
        self.assertEqual(asr.hub, contract_asr["hub"])
        self.assertEqual(asr.trust_remote_code, contract_asr["trust_remote_code"])
        self.assertEqual(asr.forced_aligner, contract_asr["forced_aligner"])

    def test_ffmpeg_paths_come_from_contract(self) -> None:
        contract_paths = tuple(load_contract()["ffmpegPaths"])
        self.assertEqual(_common_ffmpeg_paths(), contract_paths)


if __name__ == "__main__":
    unittest.main()
