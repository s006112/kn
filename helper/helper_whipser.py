"""
Whisper model helpers.

This module provides a lightweight `WhisperService` wrapper plus singleton-style
accessors used across the pipeline and tools (notably the `large-v3-turbo`
configuration).
Location: `helper/helper_whipser.py`

Used by:
- `whisper/p_audio.py` for batch audio file transcription (`get_turbo_service()`).
- `tool/tool_real_time_transcription.py` for real-time array transcription (`get_turbo_service()`).
"""

import gc
import logging
import warnings
from dataclasses import dataclass
from typing import Optional

import torch
import whisper

# Whisper logs some harmless warnings; keep them quiet.
warnings.filterwarnings(
    "ignore", message="FP16 is not supported on CPU; using FP32 instead"
)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message=r"You are using `torch.load`",
)

@dataclass
class WhisperConfig:
    """Configuration for WhisperService."""

    model_name: str

class WhisperService:
    """
    Shared Whisper model manager with simple GPU/CPU heuristics.

    The underlying model is loaded lazily and cached per-process; switching
    devices triggers a reload.
    """

    def __init__(self, config: WhisperConfig):
        self.config = config
        self._model: Optional[whisper.Whisper] = None
        self._device: Optional[str] = None

    def _run_transcribe(
        self,
        model: whisper.Whisper,
        source,
        *,
        language: Optional[str],
        task: str,
    ) -> str:
        result = model.transcribe(
            source,
            task=task,
            language=language,
        )
        return result.get("text", "")

    def _clear_gpu(self) -> None:
        gc.collect()
        torch.cuda.empty_cache()

    # --- Model loading -----------------------------------------------
    def _load_model(self, device: str) -> whisper.Whisper:
        if self._model is None or self._device != device:
            self._model = whisper.load_model(self.config.model_name, device=device)
            self._device = device
        return self._model

    def load_model(self) -> whisper.Whisper:
        """
        Initialize Whisper, preferring a dedicated GPU when available.

        Returns the cached model instance, loading it the first time it is
        requested for a given device.
        """
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

    # --- Public transcription APIs ----------------------------------
    def transcribe_file(
        self,
        wav_path: str,
        *,
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> str:
        """
        Transcribe a mono 16kHz WAV file. Mirrors the behavior in `p_audio.py`:
        - prefer GPU when available
        - on GPU OOM, fall back to CPU and retry
        """
        model = self.load_model()
        device = self._device or "cpu"

        try:
            text = self._run_transcribe(
                model,
                wav_path,
                language=language,
                task=task,
            )
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() and device == "cuda":
                logging.error("GPU OOM, retrying Whisper on CPU")
                self._clear_gpu()
                model = self._load_model("cpu")
                text = self._run_transcribe(
                    model,
                    wav_path,
                    language=language,
                    task=task,
                )
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
        """
        Transcribe an in-memory audio array (typically float32 PCM).

        Notes:
        - `audio` is expected to already be 16kHz mono (Whisper's default).
        - `sample_rate` is currently unused and is kept for call-site clarity
          and future resampling support.
        - Unlike `transcribe_file`, this path does not retry on GPU OOM.
        """
        model = self.load_model()
        return self._run_transcribe(
            model,
            audio,
            language=language,
            task=task,
        )


_DEFAULT_SERVICE: Optional[WhisperService] = None


def get_default_service(config: WhisperConfig) -> WhisperService:
    """
    Return a process-wide WhisperService singleton.

    The first caller may pass a config; later calls return the same instance and
    ignore config changes.
    """
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = WhisperService(config)
    return _DEFAULT_SERVICE


# Convenience config/service for the common large-v3-turbo setup used across tools.
DEFAULT_TURBO_CONFIG = WhisperConfig(
    model_name="large-v3-turbo",
)


def get_turbo_service() -> WhisperService:
    """
    Return the process-wide WhisperService configured for `large-v3-turbo`.

    This is a convenience wrapper around `get_default_service` using
    `DEFAULT_TURBO_CONFIG`.
    """
    return get_default_service(DEFAULT_TURBO_CONFIG)
