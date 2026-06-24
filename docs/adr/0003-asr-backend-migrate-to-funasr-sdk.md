# ASR backend migrates from llama.cpp binary back to funasr Python SDK

We replaced the FunASR llama.cpp / GGUF runtime binaries (`llama-funasr-vad` + `llama-funasr-sensevoice`) introduced in [ADR 0002](0002-asr-backend-migrate-to-llamacpp.md) with the official `funasr` Python SDK (`pip install funasr`). ADR 0002 migrated *away* from the Python SDK to eliminate the single heaviest dependency (PyTorch); this ADR reverts that decision because the binary path introduced quality regressions that the SDK resolves cleanly.

## Why we went back

The llama.cpp binaries exposed three problems on real-world audio that all stemmed from the binary being a minimal port of SenseVoice without the SDK's supporting pipeline:

1. **Cross-language hallucinations.** The binary has no `--language` flag; SenseVoice auto-detects language per segment and, on faint/ambiguous audio (e.g. a 1-second pause), hallucinated Chinese words ("高级上", "申请上", "王税") in an otherwise English video. The SDK's `generate(language="en")` constrains detection and eliminates the hallucinations (verified on the same audio).
2. **No punctuation.** The binary's `--keep-tags` path emits raw tokens with no sentence punctuation. This made long VAD segments (up to 30s, see below) impossible to split by sentence, and forced a fragile "subdivide long segments by character-count ratio" heuristic (the deleted scheme E). The SDK pairs SenseVoice with `punc_model="ct-punc"` to restore punctuation (121 punctuation marks recovered on the test audio).
3. **Hard-coded 30s `max_seg`.** The standalone `llama-funasr-vad` binary compiles with `max_seg=30000ms` and ignores `--vad-maxseg`; only the SenseVoice binary honors it, so the two diverge in segment count. This required an entire `_build_cues` pairing/alignment layer. The SDK's `vad_kwargs={"max_single_segment_time": 8000}` controls segmentation in one place and returns `sentence_info` with start/end timestamps directly.

## What changed

`FunasrAsrBackend` (`src/subtitle_llm/media/asr_backend.py`) replaces `LlamacppAsrBackend`. It implements the same `AsrBackend` protocol and produces `AsrCue` objects, so `transcriber.py` and the translation pipeline are unchanged. The new backend constructs a single `funasr.AutoModel` combining SenseVoice + fsmn-vad + ct-punc + cam++ (`cam++` triggers `sentence_info` output, which carries per-segment timestamps).

Deleted with the binary path: the VAD/SenseVoice two-binary invocation, the `--vad-maxseg` tuning, the segment-count pairing logic, the character-ratio "scheme E" subdivision, and the standalone `llama-funasr-vad` / `llama-funasr-sensevoice` binaries under `vendor/funasr/`.

## Trade-off accepted

The SDK reintroduces **PyTorch** (the dependency ADR 0002 removed). We accept this because:
- SenseVoice runs at **17× realtime on CPU** per FunASR's benchmark; no GPU required for typical subtitle-length audio.
- The quality gains (no hallucinations, punctuation, clean timestamps) outweigh the dependency cost for a tool whose primary output quality is the ASR transcript.
- A future `funasr-onnx` path (documented by FunASR) can later remove PyTorch again if distribution weight becomes a concern.

Configuration moved from binary paths (`vad_binary`, `sensevoice_binary`, `model_dir`, `model_path()`) to SDK parameters (`model_name`, `punc_model`, `spk_model`, `max_single_segment_time`, `device`, `hub`, `trust_remote_code`). First run auto-downloads models (~1GB) to the HuggingFace cache; `scripts/setup-funasr.sh` is now a one-line `pip install funasr`.
