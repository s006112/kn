"""
Responsibility:
Provide a small wrapper around OpenAI Whisper model loading and transcription with
automatic device selection (CUDA when available) and GPU-friendly inference
defaults for running large models on constrained VRAM without CPU offloading.

Used by:
* tool/tool_real_time_transription.py
* whisper/p_audio.py

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
        """Purpose:
        Store the Whisper model name and initialize lazy-loaded state.

        Inputs:
        - model_name: Whisper model identifier passed to `whisper.load_model`.

        Outputs:
        - None.

        Side effects:
        - Initializes internal cache fields (`_model`, `_device`).

        Failure modes:
        - None (constructor does not load the model).
        """
        self.model_name = model_name
        self._model: Optional[whisper.Whisper] = None
        self._device: Optional[str] = None

    def _load_model(self, device: str) -> whisper.Whisper:
        """Purpose:
        Load and cache a Whisper model instance for the requested device.

        Inputs:
        - device: Device string passed to `whisper.load_model` (for example, `cpu`
          or `cuda`).

        Outputs:
        - A `whisper.Whisper` model instance loaded on `device`.

        Side effects:
        - May load model weights and allocate CPU/GPU memory.
        - Updates `_model` and `_device` cache fields.

        Failure modes:
        - Propagates exceptions raised by `whisper.load_model`.
        """
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
        """Purpose:
        Select an execution device and ensure the Whisper model is loaded.

        Inputs:
        - None.

        Outputs:
        - A `whisper.Whisper` model instance loaded on the chosen device.

        Side effects:
        - Logs device selection and model loading.
        - May allocate CPU/GPU memory to load the model.

        Failure modes:
        - Propagates exceptions raised by CUDA queries or model loading; GPU
          device-name probing errors are logged and treated as CPU fallback.
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

    def transcribe_file(
        self,
        wav_path: str,
        *,
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> str:
        """Purpose:
        Transcribe an audio file path into text using Whisper.

        Inputs:
        - wav_path: Path to an audio file accepted by `whisper.Whisper.transcribe`.
        - language: Optional language hint passed through to Whisper.
        - task: Whisper task name passed through to Whisper (for example,
          `transcribe` or `translate`).

        Outputs:
        - The recognized text from Whisper's `text` field (empty string if absent).

        Side effects:
        - Loads the Whisper model on first use.
        - Logs device decisions and GPU OOM fallback.
        - On CUDA out-of-memory, triggers GC and clears the CUDA cache before
          reloading the model on CPU.

        Failure modes:
        - Re-raises `RuntimeError` for non-OOM failures or when not on CUDA.
        - Propagates exceptions from file handling and Whisper transcription.
        """
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
        """Purpose:
        Transcribe an in-memory audio array into text using Whisper.

        Inputs:
        - audio: Audio array accepted by `whisper.Whisper.transcribe`.
        - sample_rate: Provided by callers but not used by this function.
        - language: Optional language hint passed through to Whisper.
        - task: Whisper task name passed through to Whisper.

        Outputs:
        - The recognized text from Whisper's `text` field (empty string if absent).

        Side effects:
        - Loads the Whisper model on first use.

        Failure modes:
        - Propagates exceptions from Whisper transcription.
        """
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
def get_service() -> WhisperService:
    """Purpose:
    Provide a cached `WhisperService` instance for the default model.

    Inputs:
    - None.

    Outputs:
    - A process-wide cached `WhisperService` configured for `large-v3-turbo`.

    Side effects:
    - Constructs and caches a `WhisperService` instance on first call.

    Failure modes:
    - None (construction is lazy and does not load model weights).
    """
    return WhisperService("large-v3-turbo")
