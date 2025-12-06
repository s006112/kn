import numpy as np
import torch
from transformers import AutoConfig, AutoModel


def l2_normalize(mat: list[list[float]]) -> list[list[float]]:
    """L2-normalize a batch of vectors to unit length."""
    arr = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr.tolist()


class EmbeddingModel:
    """Lightweight wrapper around a Transformers model with encode()."""

    def __init__(self, model_name: str, device: str, batch_size: int, task: str):
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._task = task

        config = AutoConfig.from_pretrained(
            model_name,
            trust_remote_code=True,
        )
        if getattr(config, "use_flash_attn", False):
            print("ℹ️ Disabling flash attention for this model; using PyTorch attention instead")
            config.use_flash_attn = False

        self._model = AutoModel.from_pretrained(
            model_name,
            trust_remote_code=True,
            config=config,
        )
        if torch.cuda.is_available() and device.startswith("cuda"):
            self._model.to(device)
        self._model.eval()

    def _encode(self, texts: list[str]):
        with torch.no_grad():
            vectors = self._model.encode(
                texts,
                batch_size=self._batch_size,
                task=self._task,
                device=self._device,
            )
        if torch.is_tensor(vectors):
            return vectors.detach().cpu().numpy()
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._encode(texts)
        if isinstance(vectors, np.ndarray):
            return vectors.tolist()
        return [list(vec) for vec in vectors]

    def embed_query(self, text: str) -> np.ndarray:
        vectors = self._encode([text])
        if isinstance(vectors, np.ndarray):
            vec = vectors[0]
        elif torch.is_tensor(vectors):
            vec = vectors[0].detach().cpu().numpy()
        else:
            vec = np.asarray(vectors[0], dtype=np.float32)
        vec = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec


def build_embeddings(model_name: str, device: str, batch_size: int, task: str) -> EmbeddingModel:
    return EmbeddingModel(
        model_name=model_name,
        device=device,
        batch_size=batch_size,
        task=task,
    )
