"""desktop-contract.json 与 Python 侧默认值的一致性锁定。

契约文件是桌面端生成 YAML 的单一来源；这里的测试保证 Python 侧
settings.py 的 ASRConfig 默认值与契约不漂移（历史上漂移真实发生过：
桌面端曾静默覆盖 ASR 默认模型为 SenseVoiceSmall）。
"""

from __future__ import annotations

import json
import unittest
from importlib import resources

from subtitle_llm.media.asr_models import (
    default_asr_model_id,
    list_asr_models,
    resolve_asr_config,
)
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
        self.assertEqual(asr.language_style, contract_asr["language_style"])
        self.assertEqual(asr.generate_kwargs, contract_asr["generate_kwargs"])

    def test_ffmpeg_paths_come_from_contract(self) -> None:
        contract_paths = tuple(load_contract()["ffmpegPaths"])
        self.assertEqual(_common_ffmpeg_paths(), contract_paths)

    def test_asr_profiles_match_contract(self) -> None:
        """注册表里的 profile 与契约 asrModels 逐字段一致。"""
        contract = load_contract()
        contract_profiles = {item["id"]: item for item in contract["asrModels"]}
        registry_profiles = {profile.id: profile for profile in list_asr_models()}
        self.assertEqual(set(registry_profiles), set(contract_profiles))
        for profile_id, item in contract_profiles.items():
            profile = registry_profiles[profile_id]
            for key, value in item.items():
                self.assertEqual(getattr(profile, key), value, f"{profile_id}.{key}")
        self.assertEqual(default_asr_model_id(), contract["defaultAsrModel"])

    def test_resolve_asr_config_profile_fills_unset_fields(self) -> None:
        config = resolve_asr_config(profile="paraformer-zh")
        self.assertEqual(config.model_name, "paraformer-zh")
        self.assertEqual(config.hub, "ms")
        self.assertEqual(config.punc_model, "ct-punc")
        self.assertEqual(config.language_style, "code")

    def test_resolve_asr_config_profile_wins_over_yaml(self) -> None:
        """--asr-model 是明确选择：profile 整段胜出，忽略 YAML 的 asr 字段。"""
        base = ASRConfig(model_name="FunAudioLLM/Fun-ASR-Nano-2512", hub="hf")
        config = resolve_asr_config(base, profile="paraformer-zh")
        self.assertEqual(config.model_name, "paraformer-zh")
        self.assertEqual(config.hub, "ms")
        self.assertEqual(config.spk_model, "cam++")

    def test_resolve_asr_config_keeps_explicit_device(self) -> None:
        """device 按机器选择，不属于 profile；base 显式 device 与参数覆盖都保留。"""
        base = ASRConfig(device="cuda", max_single_segment_time=15000)
        config = resolve_asr_config(base, profile="fun-asr-nano")
        self.assertEqual(config.device, "cuda")
        self.assertEqual(config.max_single_segment_time, 8000)
        self.assertEqual(config.model_name, "FunAudioLLM/Fun-ASR-Nano-2512")
        self.assertEqual(resolve_asr_config(base, device="mps").device, "mps")

    def test_resolve_asr_config_without_profile_uses_base(self) -> None:
        base = ASRConfig(model_name="paraformer-zh", hub="ms")
        config = resolve_asr_config(base)
        self.assertEqual(config.model_name, "paraformer-zh")

    def test_resolve_asr_config_unknown_profile_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_asr_config(profile="not-a-model")


if __name__ == "__main__":
    unittest.main()
