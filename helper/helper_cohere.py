"""
Responsibility:
Provide a small wrapper around Cohere Transcribe model loading and transcription
with automatic device selection (CUDA when available) and a cached service
interface compatible with the existing Whisper helper usage.

Used by:
* drop-in alternative for helper/helper_whisper.py callers

Pipelines:
- audio_input -> load_model -> cohere_transcribe -> text_output

Invariants:
- `CohereService._device` tracks the device of the currently cached model.
- `get_service()` returns a process-wide cached `CohereService` instance.

Out of scope:
- Audio decoding, resampling, or normalization before transcription.
- Streaming or chunked decoding orchestration outside the model helper.
- Returning model metadata beyond the transcribed text.
"""

import gc
import logging
from functools import lru_cache
from typing import Optional

import torch
from huggingface_hub import hf_hub_download
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor


class CohereService:
    """Thin wrapper around Cohere model loading and transcription."""

    def __init__(self, model_name: str):
        """Purpose:
        Store the Cohere model name and initialize lazy-loaded state.

        Inputs:
        - model_name: Hugging Face model identifier.

        Outputs:
        - None.

        Side effects:
        - Initializes internal cache fields (`_processor`, `_model`, `_device`).

        Failure modes:
        - None (constructor does not load the model).
        """
        self.model_name = model_name
        self._processor = None
        self._model = None
        self._device: Optional[str] = None

    def _load_model(self, device: str):
        """Purpose:
        Load and cache a Cohere processor/model pair for the requested device.

        Inputs:
        - device: Device string for model placement (`cpu` or `cuda`).

        Outputs:
        - The loaded model instance.

        Side effects:
        - May load model weights and allocate CPU/GPU memory.
        - Updates `_processor`, `_model`, and `_device` cache fields.

        Failure modes:
        - Propagates exceptions raised by model or processor loading.
        """
        if self._model is None or self._device != device or self._processor is None:
            try:
                processor = AutoProcessor.from_pretrained(
                    self.model_name,
                    trust_remote_code=True,
                    token=True,
                )
                # Ensure the large weight file is fully cached before model load.
                hf_hub_download(
                    repo_id=self.model_name,
                    filename="model.safetensors",
                    token=True,
                )
                model = AutoModelForSpeechSeq2Seq.from_pretrained(
                    self.model_name,
                    trust_remote_code=True,
                    token=True,
                ).to(device)
            except OSError as exc:
                raise RuntimeError(
                    "Failed to load Cohere model 'CohereLabs/cohere-transcribe-03-2026'. "
                    "The model weights may still be downloading or the local Hugging Face cache "
                    f"may be incomplete. Original error: {exc}"
                ) from exc
            model.eval()
            self._processor = processor
            self._model = model
            self._device = device
        return self._model

    def load_model(self):
        """Purpose:
        Select an execution device and ensure the Cohere model is loaded.

        Inputs:
        - None.

        Outputs:
        - A loaded Cohere model instance on the chosen device.

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
                logging.info("Cohere: using GPU %s", gpu_name)
            except Exception as exc:  # pragma: no cover - defensive
                logging.error("Cohere: GPU query failed: %s. Falling back to CPU.", exc)
            else:
                device = "cuda"
        logging.info("Loading Cohere model on %s", device.upper())
        return self._load_model(device)

    def transcribe_file(
        self,
        wav_path: str,
        *,
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> str:
        """Purpose:
        Transcribe an audio file path into text using Cohere Transcribe.

        Inputs:
        - wav_path: Path to an audio file accepted by `model.transcribe`.
        - language: Optional language hint forwarded when provided.
        - task: Compatibility parameter preserved for interface parity.

        Outputs:
        - The recognized text as a plain string.

        Side effects:
        - Loads the Cohere model on first use.
        - Logs device decisions and GPU OOM retry handling.

        Failure modes:
        - Re-raises `RuntimeError` for non-OOM failures or when not on CUDA.
        - Propagates exceptions from file handling and model transcription.
        """
        del task
        model = self.load_model()
        processor = self._processor
        device = self._device or "cpu"
        kwargs = {
            "processor": processor,
            "audio_files": [wav_path],
        }
        if language is not None:
            kwargs["language"] = language

        try:
            with torch.inference_mode():
                result = model.transcribe(**kwargs)
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() and device == "cuda":
                logging.error(
                    "GPU OOM during Cohere transcription; clearing cache and retrying on CUDA"
                )
                gc.collect()
                torch.cuda.empty_cache()
                with torch.inference_mode():
                    result = model.transcribe(**kwargs)
            else:
                raise
        return result[0] if result else ""

    def transcribe_array(
        self,
        audio,
        sample_rate: int,
        *,
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> str:
        """Purpose:
        Transcribe an in-memory audio array into text using Cohere Transcribe.

        Inputs:
        - audio: Audio array accepted by `model.transcribe`.
        - sample_rate: Sampling rate for `audio`.
        - language: Optional language hint forwarded when provided.
        - task: Compatibility parameter preserved for interface parity.

        Outputs:
        - The recognized text as a plain string.

        Side effects:
        - Loads the Cohere model on first use.

        Failure modes:
        - Propagates exceptions from model transcription.
        """
        del task
        model = self.load_model()
        processor = self._processor
        device = self._device or "cpu"
        kwargs = {
            "processor": processor,
            "audio_arrays": [audio],
            "sample_rates": [sample_rate],
        }
        if language is not None:
            kwargs["language"] = language

        try:
            with torch.inference_mode():
                result = model.transcribe(**kwargs)
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() and device == "cuda":
                logging.error(
                    "GPU OOM during Cohere transcription; clearing cache and retrying on CUDA"
                )
                gc.collect()
                torch.cuda.empty_cache()
                with torch.inference_mode():
                    result = model.transcribe(**kwargs)
            else:
                raise
        return result[0] if result else ""


@lru_cache(maxsize=1)
def get_service() -> CohereService:
    """Purpose:
    Provide a cached `CohereService` instance for the default model.

    Inputs:
    - None.

    Outputs:
    - A process-wide cached `CohereService` configured for the Cohere model.

    Side effects:
    - Constructs and caches a `CohereService` instance on first call.

    Failure modes:
    - None (construction is lazy and does not load model weights).
    """
    return CohereService("CohereLabs/cohere-transcribe-03-2026")
