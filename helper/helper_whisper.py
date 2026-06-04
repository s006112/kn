"""
Responsibility:
Provide a small wrapper around OpenAI Whisper model loading and transcription with
automatic device selection (CUDA when available) and GPU-friendly inference
defaults for running large models on constrained VRAM without CPU offloading.

Used by:
* tool/tool_real_time_transription.py
* w/p_audio.py

Pipelines:
- audio_input -> load_model -> whisper_transcribe -> text_output

Invariants:
- `WhisperService._device` tracks the device of the currently cached model.
- `get_service()` returns a process-wide cached `WhisperService` instance.

Out of scope:
- Audio decoding, resampling, or normalization before transcription.
- Streaming or chunked decoding, VAD, diarization, or timestamp extraction.
- Returning Whisper metadata (segments, language probabilities, etc.).
"""

import gc
import logging
import warnings
from functools import lru_cache
from typing import Optional

import torch
import whisper
import whisper.model

# Whisper logs some harmless warnings; keep them quiet.
warnings.filterwarnings(
    "ignore", message="FP16 is not supported on CPU; using FP32 instead"
)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message=r"You are using `torch.load`",
)


class WhisperService:
    """Thin wrapper around Whisper model loading and transcription."""

    def __init__(self, model_name: str):
        """Store model name; actual Whisper model is lazy-loaded."""
        self.model_name = model_name
        self._model: Optional[whisper.Whisper] = None
        self._device: Optional[str] = None

    def _load_model(self, device: str) -> whisper.Whisper:
        """Load and cache the model for `device`; CUDA uses FP16 to save VRAM."""
        if self._model is None or self._device != device:
            if device == "cuda":
                # Load on CPU first to avoid transient GPU peak from FP32 weights,
                # then move to CUDA with FP16 weights to keep VRAM headroom.
                model = whisper.load_model(self.model_name, device="cpu")
                model = model.to(device=device, dtype=torch.float16)
                # Whisper's LayerNorm forward path upcasts activations to FP32, so its
                # weights/biases must remain FP32 to avoid dtype mismatch.
                for module in model.modules():
                    if isinstance(module, whisper.model.LayerNorm):
                        module.float()
            else:
                model = whisper.load_model(self.model_name, device=device)

            model.eval()
            self._model = model
            self._device = device
        return self._model

    def load_model(self) -> whisper.Whisper:
        """Pick CUDA if available, otherwise CPU, then load the model."""
        device = "cpu"
        if torch.cuda.is_available():
            try:
                gpu_name = torch.cuda.get_device_name(0)
                logging.info("Whisper: using GPU %s", gpu_name)
            except Exception as exc:  # pragma: no cover - defensive
                logging.error("Whisper: GPU query failed: %s. Falling back to CPU.", exc)
            else:
                device = "cuda"
        logging.info("Loading Whisper model on %s", device.upper())
        return self._load_model(device)

    def transcribe_file(
        self,
        wav_path: str,
        *,
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> str:
        """Transcribe a file path; CUDA OOM clears cache and retries once."""
        model = self.load_model()
        device = self._device or "cpu"

        try:
            with torch.inference_mode():
                result = model.transcribe(
                    wav_path,
                    task=task,
                    language=language,
                )
                text = result.get("text", "")
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() and device == "cuda":
                logging.error("GPU OOM during Whisper transcription; clearing cache and retrying on CUDA")
                gc.collect()
                torch.cuda.empty_cache()
                with torch.inference_mode():
                    result = model.transcribe(
                        wav_path,
                        task=task,
                        language=language,
                    )
                    text = result.get("text", "")
            else:
                raise
        return text

    def transcribe_array(
        self,
        audio,
        sample_rate: int,
        *,
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> str:
        """Transcribe an audio array; `sample_rate` is kept for caller compatibility."""
        model = self.load_model()
        device = self._device or "cpu"
        try:
            with torch.inference_mode():
                result = model.transcribe(
                    audio,
                    task=task,
                    language=language,
                )
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() and device == "cuda":
                logging.error("GPU OOM during Whisper transcription; clearing cache and retrying on CUDA")
                gc.collect()
                torch.cuda.empty_cache()
                with torch.inference_mode():
                    result = model.transcribe(
                        audio,
                        task=task,
                        language=language,
                    )
            else:
                raise
        return result.get("text", "")


@lru_cache(maxsize=1)
def get_service(model_name: str = "large-v3-turbo") -> WhisperService:
    """Return the process-wide cached Whisper service."""
    return WhisperService(model_name)
