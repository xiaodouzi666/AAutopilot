"""Typed, shell-free command construction for ``llama-server``.

The builder deliberately owns the accelerator-related arguments.  Callers cannot
override them through ``extra_args`` and accidentally turn a CPU-only benchmark
into a GPU-offloaded run.
"""

from __future__ import annotations

import ipaddress
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


class CommandConfigurationError(ValueError):
    """Raised when a server command would be ambiguous or unsafe."""


_PROTECTED_OPTIONS = {
    "--host",
    "--port",
    "--model",
    "-m",
    "--alias",
    "--threads",
    "-t",
    "--batch-size",
    "-b",
    "--ubatch-size",
    "-ub",
    "--ctx-size",
    "-c",
    "--parallel",
    "-np",
    "--seed",
    "-lv",
    "--verbosity",
    "--log-verbosity",
    "--n-gpu-layers",
    "-ngl",
    "--gpu-layers",
    "--device",
    "--split-mode",
    "--tensor-split",
    "--metrics",
    "--no-webui",
}


def is_loopback_host(host: str) -> bool:
    """Return whether *host* is an explicit local-loopback address/name."""

    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class LlamaServerCapabilities:
    """Options detected from the pinned binary's ``--help`` output."""

    device: bool = True
    gpu_layers: bool = True
    metrics: bool = True
    no_webui: bool = True

    @classmethod
    def from_help(cls, help_text: str) -> LlamaServerCapabilities:
        return cls(
            device="--device" in help_text,
            gpu_layers=("--n-gpu-layers" in help_text or "-ngl" in help_text),
            metrics="--metrics" in help_text,
            no_webui="--no-webui" in help_text,
        )


def inspect_llama_server_capabilities(
    binary: Path, timeout_s: float = 30.0
) -> LlamaServerCapabilities:
    """Read the pinned binary's help text and reject an incompatible executable."""

    try:
        completed = subprocess.run(
            [str(binary), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise CommandConfigurationError(f"cannot inspect llama-server interface: {binary}") from exc
    text = "\n".join((completed.stdout, completed.stderr))
    if completed.returncode not in {0, 1} or "--model" not in text:
        raise CommandConfigurationError(f"cannot inspect llama-server interface: {binary}")
    return LlamaServerCapabilities.from_help(text)


@dataclass(frozen=True, slots=True)
class LlamaServerConfig:
    """Validated settings for one CPU-only ``llama-server`` instance."""

    binary: Path
    model: Path
    host: str = "127.0.0.1"
    port: int = 18080
    model_alias: str = "a64pilot-model"
    threads: int = 1
    batch_size: int = 256
    ubatch_size: int = 128
    context_size: int = 2048
    parallel: int = 1
    seed: int = 20260813
    log_verbosity: int = 4
    cpu_only: bool = True
    enable_metrics: bool = True
    disable_webui: bool = True
    affinity: tuple[int, ...] | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "binary", Path(self.binary))
        object.__setattr__(self, "model", Path(self.model))
        if not self.host or any(ch.isspace() for ch in self.host):
            raise CommandConfigurationError("host must be a hostname or address without whitespace")
        if not (1 <= self.port <= 65535):
            raise CommandConfigurationError("port must be in the range 1..65535")
        for name in ("threads", "batch_size", "ubatch_size", "context_size", "parallel"):
            if getattr(self, name) < 1:
                raise CommandConfigurationError(f"{name} must be positive")
        if not 0 <= self.log_verbosity <= 5:
            raise CommandConfigurationError("log_verbosity must be in the range 0..5")
        if self.ubatch_size > self.batch_size:
            raise CommandConfigurationError("ubatch_size must not exceed batch_size")
        if not self.model_alias.strip():
            raise CommandConfigurationError("model_alias must not be empty")
        if self.affinity is not None:
            if not self.affinity or any(cpu < 0 for cpu in self.affinity):
                raise CommandConfigurationError("affinity must contain non-negative CPU indices")
            if len(set(self.affinity)) != len(self.affinity):
                raise CommandConfigurationError("affinity CPU indices must be unique")
        self._validate_extra_args(self.extra_args)

    @staticmethod
    def _validate_extra_args(args: Sequence[str]) -> None:
        for arg in args:
            if "\x00" in arg:
                raise CommandConfigurationError("command arguments may not contain NUL bytes")
            option = arg.split("=", 1)[0]
            if option in _PROTECTED_OPTIONS:
                raise CommandConfigurationError(
                    f"{option} is managed by LlamaServerConfig and cannot appear in extra_args"
                )


@dataclass(frozen=True, slots=True)
class CommandProof:
    """Machine-readable proof derived from the constructed command."""

    cpu_only_requested: bool
    device_none: bool
    gpu_layers_zero: bool
    localhost: bool

    @property
    def cpu_only_flags_complete(self) -> bool:
        return self.cpu_only_requested and self.device_none and self.gpu_layers_zero

    def as_dict(self) -> dict[str, bool]:
        return {
            "cpu_only_requested": self.cpu_only_requested,
            "device_none": self.device_none,
            "gpu_layers_zero": self.gpu_layers_zero,
            "cpu_only_flags_complete": self.cpu_only_flags_complete,
            "localhost": self.localhost,
        }


@dataclass(frozen=True, slots=True)
class LlamaServerCommand:
    """A command plus the CPU-only intent that produced it."""

    argv: tuple[str, ...]
    proof: CommandProof

    def as_list(self) -> list[str]:
        return list(self.argv)


def build_llama_server_command(
    config: LlamaServerConfig,
    capabilities: LlamaServerCapabilities | None = None,
) -> LlamaServerCommand:
    """Build an argv vector; never a shell command string.

    CPU-only mode requires both available safeguards.  If a pinned binary lacks
    one of them, command construction fails explicitly so a benchmark cannot be
    mislabeled as CPU-only.
    """

    caps = capabilities or LlamaServerCapabilities()
    argv = [
        str(config.binary),
        "--model",
        str(config.model),
        "--alias",
        config.model_alias,
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--threads",
        str(config.threads),
        "--batch-size",
        str(config.batch_size),
        "--ubatch-size",
        str(config.ubatch_size),
        "--ctx-size",
        str(config.context_size),
        "--parallel",
        str(config.parallel),
        "--seed",
        str(config.seed),
        "-lv",
        str(config.log_verbosity),
    ]

    device_none = False
    gpu_layers_zero = False
    if config.cpu_only:
        if not caps.device or not caps.gpu_layers:
            missing = []
            if not caps.device:
                missing.append("--device")
            if not caps.gpu_layers:
                missing.append("--n-gpu-layers")
            raise CommandConfigurationError(
                "pinned llama-server cannot express complete CPU-only intent; "
                f"missing {', '.join(missing)}"
            )
        argv.extend(("--device", "none", "--n-gpu-layers", "0"))
        device_none = True
        gpu_layers_zero = True

    if config.enable_metrics and caps.metrics:
        argv.append("--metrics")
    if config.disable_webui and caps.no_webui:
        argv.append("--no-webui")
    argv.extend(config.extra_args)

    return LlamaServerCommand(
        argv=tuple(argv),
        proof=CommandProof(
            cpu_only_requested=config.cpu_only,
            device_none=device_none,
            gpu_layers_zero=gpu_layers_zero,
            localhost=is_loopback_host(config.host),
        ),
    )


def find_protected_overrides(args: Iterable[str]) -> tuple[str, ...]:
    """Expose override detection for configuration validation/tests."""

    return tuple(arg for arg in args if arg.split("=", 1)[0] in _PROTECTED_OPTIONS)
