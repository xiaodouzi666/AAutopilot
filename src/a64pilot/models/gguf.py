"""Bounded GGUF header parsing and reviewed tensor-inventory verification."""

from __future__ import annotations

import hashlib
import json
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from .registry import ModelSpec


class GgufError(ValueError):
    """Raised when a GGUF header is malformed, truncated, or unreasonable."""


_METADATA_SCALAR_BYTES = {
    0: 1,  # UINT8
    1: 1,  # INT8
    2: 2,  # UINT16
    3: 2,  # INT16
    4: 4,  # UINT32
    5: 4,  # INT32
    6: 4,  # FLOAT32
    7: 1,  # BOOL
    10: 8,  # UINT64
    11: 8,  # INT64
    12: 8,  # FLOAT64
}
_GGML_TYPE_NAMES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    9: "Q8_1",
    10: "Q2_K",
    11: "Q3_K",
    12: "Q4_K",
    13: "Q5_K",
    14: "Q6_K",
    15: "Q8_K",
    16: "IQ2_XXS",
    17: "IQ2_XS",
    18: "IQ3_XXS",
    19: "IQ1_S",
    20: "IQ4_NL",
    21: "IQ3_S",
    22: "IQ2_S",
    23: "IQ4_XS",
    24: "I8",
    25: "I16",
    26: "I32",
    27: "I64",
    28: "F64",
    29: "IQ1_M",
    30: "BF16",
    31: "Q4_0_4_4",
    32: "Q4_0_4_8",
    33: "Q4_0_8_8",
    34: "TQ1_0",
    35: "TQ2_0",
}
_MAX_METADATA_PAIRS = 1_000_000
_MAX_TENSORS = 1_000_000
_MAX_STRING_BYTES = 256 * 1024 * 1024
_MAX_ARRAY_ELEMENTS = 100_000_000
_MAX_DIMENSIONS = 16


@dataclass(frozen=True, slots=True)
class GgufTensor:
    name: str
    tensor_type: str
    dimensions: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.tensor_type,
            "dimensions": list(self.dimensions),
        }


@dataclass(frozen=True, slots=True)
class GgufInventory:
    version: int
    tensors: tuple[GgufTensor, ...]

    @property
    def histogram(self) -> tuple[tuple[str, int], ...]:
        counts = Counter(tensor.tensor_type for tensor in self.tensors)
        return tuple(sorted(counts.items()))

    @property
    def sha256(self) -> str:
        payload = {
            "version": self.version,
            "tensors": [tensor.to_dict() for tensor in self.tensors],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelInventoryProof:
    model_id: str
    model_sha256: str
    inventory_sha256: str | None
    tensor_histogram: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    reviewed_fallback_tensors: tuple[GgufTensor, ...] = field(default_factory=tuple)
    verified: bool = False
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "model_sha256": self.model_sha256,
            "inventory_sha256": self.inventory_sha256,
            "tensor_histogram": dict(self.tensor_histogram),
            "reviewed_fallback_tensors": [
                tensor.to_dict() for tensor in self.reviewed_fallback_tensors
            ],
            "verified": self.verified,
            "errors": list(self.errors),
        }


class _Reader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream

    def exact(self, size: int) -> bytes:
        value = self.stream.read(size)
        if len(value) != size:
            raise GgufError("GGUF header is truncated")
        return value

    def unpack(self, fmt: str) -> int:
        return int(struct.unpack(fmt, self.exact(struct.calcsize(fmt)))[0])

    def string(self) -> str:
        length = self.unpack("<Q")
        if length > _MAX_STRING_BYTES:
            raise GgufError("GGUF header string exceeds the safety limit")
        try:
            return self.exact(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GgufError("GGUF header contains a non-UTF-8 string") from exc

    def skip_metadata_value(self, value_type: int, *, depth: int = 0) -> None:
        if depth > 1:
            raise GgufError("nested GGUF metadata arrays are not supported")
        scalar_size = _METADATA_SCALAR_BYTES.get(value_type)
        if scalar_size is not None:
            self.exact(scalar_size)
            return
        if value_type == 8:  # STRING
            self.string()
            return
        if value_type == 9:  # ARRAY
            element_type = self.unpack("<I")
            element_count = self.unpack("<Q")
            if element_count > _MAX_ARRAY_ELEMENTS:
                raise GgufError("GGUF metadata array exceeds the safety limit")
            scalar_size = _METADATA_SCALAR_BYTES.get(element_type)
            if scalar_size is not None:
                total_size = scalar_size * element_count
                if total_size > _MAX_STRING_BYTES * 4:
                    raise GgufError("GGUF metadata scalar array exceeds the safety limit")
                self.exact(total_size)
                return
            for _ in range(element_count):
                self.skip_metadata_value(element_type, depth=depth + 1)
            return
        raise GgufError(f"GGUF metadata uses unknown value type {value_type}")


def parse_gguf_inventory(path: Path) -> GgufInventory:
    """Parse only the bounded header needed to inventory every tensor."""

    try:
        with path.open("rb") as stream:
            reader = _Reader(stream)
            if reader.exact(4) != b"GGUF":
                raise GgufError("file does not start with GGUF magic")
            version = reader.unpack("<I")
            if version not in {2, 3}:
                raise GgufError(f"unsupported GGUF version {version}")
            tensor_count = reader.unpack("<Q")
            metadata_count = reader.unpack("<Q")
            if tensor_count > _MAX_TENSORS:
                raise GgufError("GGUF tensor count exceeds the safety limit")
            if metadata_count > _MAX_METADATA_PAIRS:
                raise GgufError("GGUF metadata count exceeds the safety limit")
            for _ in range(metadata_count):
                reader.string()
                reader.skip_metadata_value(reader.unpack("<I"))
            tensors: list[GgufTensor] = []
            names: set[str] = set()
            for _ in range(tensor_count):
                name = reader.string()
                if name in names:
                    raise GgufError(f"GGUF contains duplicate tensor name {name}")
                names.add(name)
                dimension_count = reader.unpack("<I")
                if not 1 <= dimension_count <= _MAX_DIMENSIONS:
                    raise GgufError(f"GGUF tensor {name} has an invalid dimension count")
                dimensions = tuple(reader.unpack("<Q") for _ in range(dimension_count))
                raw_type = reader.unpack("<I")
                reader.unpack("<Q")  # data offset; tensor bytes are deliberately not read
                tensor_type = _GGML_TYPE_NAMES.get(raw_type)
                if tensor_type is None:
                    raise GgufError(f"GGUF tensor {name} uses unknown type {raw_type}")
                tensors.append(GgufTensor(name, tensor_type, dimensions))
    except OSError as exc:
        raise GgufError(f"cannot read GGUF header: {path.name}") from exc
    return GgufInventory(version=version, tensors=tuple(tensors))


def verify_model_inventory(
    path: Path,
    spec: ModelSpec,
    *,
    actual_sha256: str,
) -> ModelInventoryProof:
    """Replay the full tensor-header inventory against a pinned registry entry."""

    errors: list[str] = []
    inventory: GgufInventory | None = None
    if actual_sha256 != spec.expected_sha256:
        errors.append("model SHA-256 does not match the reviewed registry")
    try:
        inventory = parse_gguf_inventory(path)
    except GgufError as exc:
        errors.append(str(exc))
    if inventory is not None:
        if inventory.histogram != spec.expected_tensor_histogram:
            errors.append("GGUF tensor-type histogram does not match the reviewed registry")
        if inventory.sha256 != spec.expected_tensor_inventory_sha256:
            errors.append("GGUF full tensor inventory does not match the reviewed registry")
    reviewed = tuple(
        GgufTensor(item.name, item.tensor_type, item.dimensions)
        for item in spec.reviewed_kleidiai_fallbacks
    )
    return ModelInventoryProof(
        model_id=spec.model_id,
        model_sha256=actual_sha256,
        inventory_sha256=inventory.sha256 if inventory is not None else None,
        tensor_histogram=inventory.histogram if inventory is not None else (),
        reviewed_fallback_tensors=reviewed,
        verified=not errors,
        errors=tuple(errors),
    )
