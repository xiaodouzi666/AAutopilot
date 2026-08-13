"""Official GGUF model registry, acquisition, and integrity checks."""

from .checksum import (
    ChecksumResult,
    ManifestVerification,
    sha256_file,
    verify_file,
    verify_manifest,
)
from .download import (
    DownloadManifest,
    DownloadPlanItem,
    download_models,
    dry_run_manifest,
    write_manifest,
)
from .registry import (
    ModelRole,
    ModelSpec,
    default_registry,
    get_model,
    required_registry,
    resolve_filename,
)

__all__ = [
    "ChecksumResult",
    "DownloadManifest",
    "DownloadPlanItem",
    "ManifestVerification",
    "ModelRole",
    "ModelSpec",
    "default_registry",
    "dry_run_manifest",
    "download_models",
    "get_model",
    "required_registry",
    "resolve_filename",
    "sha256_file",
    "verify_file",
    "verify_manifest",
    "write_manifest",
]
