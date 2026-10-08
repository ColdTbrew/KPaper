"""Bound the native MLX work and coordinate OCR across KPaper processes."""
from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path
from typing import Callable


MLX_CACHE_BYTES = 256 * 1024 * 1024


class OCRProcessLock:
    """Only one local OCR model may be resident, including separate CLI runs."""

    def __init__(self, status: Callable[[str], None] | None = None) -> None:
        self.status = status
        self._file = None

    def acquire(self) -> None:
        # A stable location also coordinates processes with different TMPDIRs.
        path = Path("/tmp") / f"kpaper-ocr-{os.getuid()}.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self._file = os.fdopen(descriptor, "a+")
        try:
            try:
                fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if self.status:
                    self.status("다른 문서의 OCR이 끝나기를 기다리는 중")
                while True:
                    try:
                        fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        time.sleep(0.2)
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()
            self._file = None


def serialize_vision_encoders(model, mx, nn) -> None:
    """Evaluate each crop independently, without changing resolution or weights.

    The vision towers otherwise build one large lazy graph for every crop in a
    page. Keep the original feature order and only bound its execution batch.
    """

    class SerialEncoder(nn.Module):
        def __init__(self, encoder) -> None:
            super().__init__()
            self.encoder = encoder

        def __call__(self, images, *args, **kwargs):
            batch = images.shape[0]

            def slice_batch(value, index):
                if isinstance(value, mx.array) and value.ndim and value.shape[0] == batch:
                    return value[index : index + 1]
                return value

            outputs = []
            for index in range(batch):
                output = self.encoder(
                    images[index : index + 1],
                    *(slice_batch(value, index) for value in args),
                    **{key: slice_batch(value, index) for key, value in kwargs.items()},
                )
                mx.eval(output)
                outputs.append(output)
            result = outputs[0] if len(outputs) == 1 else mx.concatenate(outputs, axis=0)
            mx.eval(result)
            return result

    # These two attributes belong to the supported Unlimited/DeepSeek OCR
    # architecture; leave other user-supplied VLM architectures untouched.
    if hasattr(model, "sam_model") and hasattr(model, "vision_model"):
        chunk_sam_attention(model.sam_model, mx, nn)
        model.sam_model = SerialEncoder(model.sam_model)
        model.vision_model = SerialEncoder(model.vision_model)


def chunk_sam_attention(encoder, mx, nn, query_chunk: int = 256) -> None:
    """Keep all keys/values, but materialize relative-position bias in chunks.

    A 1024px global SAM view has 4096 queries. Its full N x N attention bias
    alone is hundreds of MB. Queries are independent, so chunking them preserves
    the attention equation and receptive field while bounding that allocation.
    """
    blocks = getattr(encoder, "blocks", [])
    if not blocks:
        return
    from mlx_vlm.models.deepseekocr.sam import Attention, add_decomposed_rel_pos

    class ChunkedAttention(nn.Module):
        def __init__(self, attention) -> None:
            super().__init__()
            self.attention = attention

        def __call__(self, images):
            attention = self.attention
            batch, height, width, _ = images.shape
            length = height * width
            if length <= query_chunk:
                return attention(images)
            heads = attention.num_heads
            qkv = attention.qkv(images).reshape(batch, length, 3, heads, -1).transpose(2, 0, 3, 1, 4)
            q, k, v = [qkv[index] for index in range(3)]
            rel_h, rel_w = add_decomposed_rel_pos(
                q.reshape(batch * heads, length, -1), attention.rel_pos_h, attention.rel_pos_w,
                (height, width), (height, width),
            )
            rel_h = rel_h.reshape(batch, heads, length, height, 1)
            rel_w = rel_w.reshape(batch, heads, length, 1, width)
            mx.eval(q, k, v, rel_h, rel_w)
            outputs = []
            for start in range(0, length, query_chunk):
                end = min(start + query_chunk, length)
                bias = (rel_h[:, :, start:end] + rel_w[:, :, start:end]).reshape(batch, heads, end-start, length)
                output = mx.fast.scaled_dot_product_attention(q[:, :, start:end], k, v,
                                                             scale=attention.scale, mask=bias)
                mx.eval(output)
                outputs.append(output)
            output = mx.concatenate(outputs, axis=2).reshape(batch, heads, height, width, -1)
            output = output.transpose(0, 2, 3, 1, 4).reshape(batch, height, width, -1)
            return attention.proj(output)

    for block in blocks:
        attention = getattr(block, "attn", None)
        if (getattr(block, "window_size", None) == 0 and isinstance(attention, Attention)
                and attention.use_rel_pos):
            block.attn = ChunkedAttention(attention)
