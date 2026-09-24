"""Pinned multilingual E5 int8 ONNX inference, entirely local after model preparation."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import httpx

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FILES = {
    "model_qint8_avx512_vnni.onnx": "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
    "tokenizer.json": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
}
# Include the pooling/window recipe in the identity, not just the upstream weights.
IDENTITY = "e5-int8-" + REVISION + "-windows-v1"


def file_hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def prepare(directory: Path):
    """Explicit setup only. Inference never downloads anything or sends text remotely."""
    directory.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for name, expected in FILES.items():
            path = directory / name
            if path.exists() and file_hash(path) == expected:
                continue
            temporary = directory / (name + ".part")
            with client.stream(
                "GET", f"https://huggingface.co/{MODEL}/resolve/{REVISION}/onnx/{name}"
            ) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for block in response.iter_bytes():
                        handle.write(block)
            if file_hash(temporary) != expected:
                raise ValueError("embedding_download_checksum_mismatch")
            temporary.replace(path)
    return {
        "engine": "e5",
        "model": MODEL,
        "model_digest": IDENTITY,
        "model_directory": str(directory.resolve()),
        "dimensions": 384,
        "minimum_similarity": 0.84,
    }


class Runtime:
    def __init__(self, directory):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        for name, expected in FILES.items():
            if file_hash(Path(directory) / name) != expected:
                raise ValueError("embedding_model_changed_reindex_required")
        self.tokenizer = Tokenizer.from_file(str(Path(directory) / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.bos = self.tokenizer.token_to_id("<s>")
        self.eos = self.tokenizer.token_to_id("</s>")
        self.pad = self.tokenizer.token_to_id("<pad>")
        if None in (self.bos, self.eos, self.pad):
            raise ValueError("invalid_embedding_tokenizer")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(Path(directory) / "model_qint8_avx512_vnni.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.inputs = {item.name for item in self.session.get_inputs()}

    def encode(self, texts, *, query=False):
        import numpy as np

        prefix = self.tokenizer.encode(
            "query: " if query else "passage: ", add_special_tokens=False
        ).ids
        capacity = 512 - len(prefix) - 2
        windows = []
        for index, text in enumerate(texts):
            tokens = self.tokenizer.encode(text, add_special_tokens=False).ids
            for offset in range(0, max(1, len(tokens)), capacity - 32):
                part = tokens[offset : offset + capacity]
                windows.append((index, [self.bos, *prefix, *part, self.eos], max(1, len(part))))
                if offset + capacity >= len(tokens):
                    break
        totals = np.zeros((len(texts), 384), dtype=np.float32)
        for start in range(0, len(windows), 8):
            batch = windows[start : start + 8]
            width = max(len(row[1]) for row in batch)
            ids = np.full((len(batch), width), self.pad, dtype=np.int64)
            mask = np.zeros_like(ids)
            for index, (_, tokens, _) in enumerate(batch):
                ids[index, : len(tokens)] = tokens
                mask[index, : len(tokens)] = 1
            inputs = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                inputs["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, inputs)[0]
            pooled = (hidden * mask[:, :, None]).sum(axis=1) / mask.sum(axis=1)[:, None]
            for (index, _, weight), vector in zip(batch, pooled, strict=True):
                totals[index] += vector * weight
        norms = np.linalg.norm(totals, axis=1, keepdims=True)
        if not np.isfinite(totals).all() or (norms == 0).any():
            raise ValueError("invalid_embedding")
        return (totals / norms).tolist()


@lru_cache(maxsize=2)
def _runtime(directory, stamps):
    return Runtime(directory)


def runtime(config):
    if config["model_digest"] != IDENTITY:
        raise ValueError("embedding_model_changed_reindex_required")
    directory = Path(config["model_directory"])
    stamps = tuple(
        (p.stat().st_mtime_ns, p.stat().st_size) for p in (directory / name for name in FILES)
    )
    return _runtime(str(directory), stamps)
