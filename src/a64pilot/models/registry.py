"""Reviewed registry for official Apache-2.0 Qwen2.5 GGUF models."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

OFFICIAL_QWEN_REPOSITORIES = frozenset(
    {
        "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
    }
)
LICENSE_ID = "Apache-2.0"
WEAK_REVISION = "9217f5db79a29953eb74d5343926648285ec7e67"
STRONG_REVISION = "91cad51170dc346986eccefdc2dd33a9da36ead9"
LICENSE_URL = f"https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/blob/{WEAK_REVISION}/LICENSE"
STRONG_LICENSE_URL = (
    f"https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/blob/{STRONG_REVISION}/LICENSE"
)


class RegistryError(ValueError):
    """Raised for unknown, ambiguous, or non-official model selections."""


class ModelRole(StrEnum):
    WEAK = "weak"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class ReviewedTensorFallback:
    """A disclosed tensor that the pinned backend cannot accelerate."""

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
class ModelSpec:
    model_id: str
    role: ModelRole
    repository: str
    quantization: str
    expected_filename: str
    expected_sha256: str
    expected_bytes: int
    expected_tensor_histogram: tuple[tuple[str, int], ...]
    expected_tensor_inventory_sha256: str
    revision: str = "main"
    license_id: str = LICENSE_ID
    license_url: str = LICENSE_URL
    kleidiai_compatible: bool = False
    reviewed_kleidiai_fallbacks: tuple[ReviewedTensorFallback, ...] = ()

    def __post_init__(self) -> None:
        if self.repository not in OFFICIAL_QWEN_REPOSITORIES:
            raise RegistryError(f"model repository is not reviewed: {self.repository}")
        if self.license_id != LICENSE_ID:
            raise RegistryError("official Qwen2.5 GGUF registry must record Apache-2.0")
        if PurePosixPath(self.expected_filename).name != self.expected_filename:
            raise RegistryError("expected model filename must not contain a directory")
        if not self.expected_filename.lower().endswith(".gguf"):
            raise RegistryError("expected model filename must be a GGUF")
        if not re.fullmatch(r"[0-9a-f]{64}", self.expected_sha256):
            raise RegistryError("expected model SHA-256 must be 64 lowercase hexadecimal digits")
        if self.expected_bytes < 1:
            raise RegistryError("expected model size must be positive")
        if not self.expected_tensor_histogram or any(
            not tensor_type or count < 1 for tensor_type, count in self.expected_tensor_histogram
        ):
            raise RegistryError("expected tensor histogram must contain positive counts")
        if tuple(sorted(self.expected_tensor_histogram)) != self.expected_tensor_histogram:
            raise RegistryError("expected tensor histogram must be sorted by tensor type")
        if not re.fullmatch(r"[0-9a-f]{64}", self.expected_tensor_inventory_sha256):
            raise RegistryError(
                "expected tensor inventory SHA-256 must be 64 lowercase hexadecimal digits"
            )
        histogram_types = {tensor_type for tensor_type, _ in self.expected_tensor_histogram}
        if any(
            fallback.tensor_type not in histogram_types
            or not fallback.name
            or not fallback.dimensions
            or any(dimension < 1 for dimension in fallback.dimensions)
            for fallback in self.reviewed_kleidiai_fallbacks
        ):
            raise RegistryError("reviewed KleidiAI fallback is absent from the tensor inventory")

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "role": self.role.value,
            "repository": self.repository,
            "revision": self.revision,
            "quantization": self.quantization,
            "expected_filename": self.expected_filename,
            "expected_sha256": self.expected_sha256,
            "expected_bytes": self.expected_bytes,
            "expected_tensor_histogram": dict(self.expected_tensor_histogram),
            "expected_tensor_inventory_sha256": self.expected_tensor_inventory_sha256,
            "license": self.license_id,
            "license_url": self.license_url,
            "kleidiai_compatible": self.kleidiai_compatible,
            "reviewed_kleidiai_fallbacks": [
                fallback.to_dict() for fallback in self.reviewed_kleidiai_fallbacks
            ],
        }


_DEFAULT_REGISTRY: tuple[ModelSpec, ...] = (
    ModelSpec(
        "weak-q4-0",
        ModelRole.WEAK,
        "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
        "Q4_0",
        "qwen2.5-0.5b-instruct-q4_0.gguf",
        "7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed",
        428_730_208,
        (("F32", 121), ("Q4_0", 169), ("Q8_0", 1)),
        "1a7569410a657c555f86436d1c00104d72953be10d4861598877f516ada30b05",
        revision=WEAK_REVISION,
        kleidiai_compatible=True,
    ),
    ModelSpec(
        "strong-q4-0",
        ModelRole.STRONG,
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "Q4_0",
        "qwen2.5-1.5b-instruct-q4_0.gguf",
        "dcd819ff094852c38faba6873d8ff0c9d51eadb2844539e52042ae5d647bbfdb",
        1_066_227_232,
        (("F32", 141), ("Q4_0", 197), ("Q6_K", 1)),
        "af0f2dacb88a77fd2ac469924b72ca1cee6ef03993a2df7e916d8ad4ea38ae76",
        revision=STRONG_REVISION,
        license_url=STRONG_LICENSE_URL,
        kleidiai_compatible=True,
        reviewed_kleidiai_fallbacks=(
            ReviewedTensorFallback("output.weight", "Q6_K", (1536, 151936)),
        ),
    ),
    ModelSpec(
        "strong-q8-0",
        ModelRole.STRONG,
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "Q8_0",
        "qwen2.5-1.5b-instruct-q8_0.gguf",
        "d7efb072e7724d25048a4fda0a3e10b04bdef5d06b1403a1c93bd9f1240a63c8",
        1_894_532_128,
        (("F32", 141), ("Q8_0", 198)),
        "d8c4f7c924053e056d95e291c4817c051ebb948a15a0f9e8aeae4a147815e758",
        revision=STRONG_REVISION,
        license_url=STRONG_LICENSE_URL,
        kleidiai_compatible=True,
    ),
)


def default_registry() -> tuple[ModelSpec, ...]:
    return _DEFAULT_REGISTRY


def required_registry() -> tuple[ModelSpec, ...]:
    """Return the minimum competition-ready weak/strong Q4_0 and reference Q8_0 set."""

    required_ids = {"weak-q4-0", "strong-q4-0", "strong-q8-0"}
    return tuple(spec for spec in _DEFAULT_REGISTRY if spec.model_id in required_ids)


def get_model(model_id: str, registry: Iterable[ModelSpec] | None = None) -> ModelSpec:
    matches = [spec for spec in (registry or _DEFAULT_REGISTRY) if spec.model_id == model_id]
    if len(matches) != 1:
        raise RegistryError(f"unknown model id: {model_id}")
    return matches[0]


def models_for_role(
    role: ModelRole | str, registry: Iterable[ModelSpec] | None = None
) -> tuple[ModelSpec, ...]:
    selected = ModelRole(role)
    return tuple(spec for spec in (registry or _DEFAULT_REGISTRY) if spec.role is selected)


def resolve_filename(spec: ModelSpec, repository_files: Sequence[str]) -> str:
    """Resolve a repository filename case-insensitively and unambiguously."""

    ggufs = [path for path in repository_files if path.lower().endswith(".gguf")]
    expected = spec.expected_filename.lower()
    exact = [path for path in ggufs if PurePosixPath(path).name.lower() == expected]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RegistryError(
            f"repository contains duplicate case-insensitive filename: {spec.expected_filename}"
        )

    quant_pattern = re.compile(
        rf"(?:^|[-_.]){re.escape(spec.quantization.lower())}(?:[-_.]|$)",
        re.IGNORECASE,
    )
    quant_matches = [path for path in ggufs if quant_pattern.search(PurePosixPath(path).stem)]
    if len(quant_matches) == 1:
        return quant_matches[0]
    if not quant_matches:
        raise RegistryError(
            f"official repository {spec.repository} has no GGUF for {spec.quantization}"
        )
    raise RegistryError(
        f"official repository {spec.repository} has ambiguous {spec.quantization} files: "
        + ", ".join(sorted(quant_matches))
    )


def validate_registry(registry: Sequence[ModelSpec]) -> None:
    ids = [spec.model_id for spec in registry]
    if len(ids) != len(set(ids)):
        raise RegistryError("model registry contains duplicate ids")
    required = {
        (ModelRole.WEAK, "Q4_0"),
        (ModelRole.STRONG, "Q4_0"),
        (ModelRole.STRONG, "Q8_0"),
    }
    present = {(spec.role, spec.quantization) for spec in registry}
    if not required.issubset(present):
        raise RegistryError("model registry lacks required weak/strong candidates")
    required_specs = [spec for spec in registry if (spec.role, spec.quantization) in required]
    if not all(spec.kleidiai_compatible for spec in required_specs):
        raise RegistryError("required competition models must be KleidiAI-compatible")


validate_registry(_DEFAULT_REGISTRY)
