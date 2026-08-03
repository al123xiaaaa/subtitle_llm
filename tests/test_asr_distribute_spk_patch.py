"""_patch_funasr_distribute_spk 的单测：用假 funasr 模块验证 None 时间戳被填充。"""

from __future__ import annotations

import sys
import types
import unittest

from subtitle_llm.media.asr_backend import _patch_funasr_distribute_spk


def _install_fake_funasr(original):
    """把假的 funasr.auto.auto_model 塞进 sys.modules，返回原模块引用。"""
    auto_model = types.ModuleType("funasr.auto.auto_model")
    auto_model.distribute_spk = original
    auto_pkg = types.ModuleType("funasr.auto")
    auto_pkg.auto_model = auto_model
    funasr_pkg = types.ModuleType("funasr")
    funasr_pkg.auto = auto_pkg
    return {"funasr": funasr_pkg, "funasr.auto": auto_pkg, "funasr.auto.auto_model": auto_model}


class PatchFunasrDistributeSpkTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: sys.modules.get(k) for k in ("funasr", "funasr.auto", "funasr.auto.auto_model")}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def test_fills_none_timestamps_and_delegates(self):
        seen = []

        def original(sentence_list, sd_time_list):
            for d in sentence_list:
                assert d["start"] is not None and d["end"] is not None
            seen.append((sentence_list, sd_time_list))
            return sentence_list

        fakes = _install_fake_funasr(original)
        sys.modules.update(fakes)

        _patch_funasr_distribute_spk()
        patched = fakes["funasr.auto.auto_model"].distribute_spk
        self.assertIsNot(patched, original)

        sentences = [
            {"start": None, "end": None, "sentence": "缺时间戳"},
            {"start": 100, "end": 200, "sentence": "正常"},
        ]
        sd_time_list = [(0.0, 1.0, 0)]
        patched(sentences, sd_time_list)

        self.assertEqual(len(seen), 1)
        self.assertEqual(sentences[0]["start"], 0)
        self.assertEqual(sentences[0]["end"], 0)

    def test_patch_is_idempotent(self):
        def original(sentence_list, sd_time_list):
            return sentence_list

        fakes = _install_fake_funasr(original)
        sys.modules.update(fakes)

        _patch_funasr_distribute_spk()
        first = fakes["funasr.auto.auto_model"].distribute_spk
        _patch_funasr_distribute_spk()
        self.assertIs(fakes["funasr.auto.auto_model"].distribute_spk, first)


if __name__ == "__main__":
    unittest.main()
