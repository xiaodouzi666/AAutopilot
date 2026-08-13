"""Lifecycle management for CPU-only ``llama-server`` processes."""

from __future__ import annotations

import atexit
import json
import os
import re
import signal
import socket
import subprocess
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import BinaryIO
from uuid import uuid4

from .health import HealthResult, ReadinessError, wait_for_http_ready
from .llama_command import (
    LlamaServerCapabilities,
    LlamaServerConfig,
    build_llama_server_command,
    is_loopback_host,
)
from .rss_sampler import RssSampler


class ProcessManagerError(RuntimeError):
    """Base class for managed-server errors."""


class PortUnavailableError(ProcessManagerError):
    """Raised before launch when the configured port is already occupied."""


class RemoteBindRefused(ProcessManagerError):
    """Raised when an accidental non-loopback bind is requested."""


_allocated_port_lock = threading.Lock()
_allocated_ports: set[tuple[str, int]] = set()


def _socket_family(host: str) -> socket.AddressFamily:
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def port_is_available(host: str, port: int) -> bool:
    """Check whether a TCP port can be bound right now.

    This is a preflight check, not a cross-process locking guarantee.  The child
    is launched immediately afterwards and any race is surfaced in its logs.
    """

    sock = socket.socket(_socket_family(host), socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def find_available_port(
    start: int = 18080,
    *,
    host: str = "127.0.0.1",
    attempts: int = 1000,
) -> int:
    """Select a deterministic free port not returned earlier in this process."""

    if not (1 <= start <= 65535):
        raise ValueError("start must be in the range 1..65535")
    if attempts < 1:
        raise ValueError("attempts must be positive")
    with _allocated_port_lock:
        for port in range(start, min(start + attempts, 65536)):
            key = (host, port)
            if key not in _allocated_ports and port_is_available(host, port):
                _allocated_ports.add(key)
                return port
    raise PortUnavailableError(f"no free port in {host}:{start}-{min(start + attempts - 1, 65535)}")


@dataclass(frozen=True, slots=True)
class ProcessArtifacts:
    stdout_log: Path
    stderr_log: Path
    rss_csv: Path
    command_json: Path


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    pid: int | None
    running: bool
    returncode: int | None
    command: tuple[str, ...]
    health_url: str
    ready: bool
    peak_rss_bytes: int
    affinity_applied: bool
    affinity_error: str | None
    cpu_only_flags_complete: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "running": self.running,
            "returncode": self.returncode,
            "command": list(self.command),
            "health_url": self.health_url,
            "ready": self.ready,
            "peak_rss_bytes": self.peak_rss_bytes,
            "affinity_applied": self.affinity_applied,
            "affinity_error": self.affinity_error,
            "cpu_only_flags_complete": self.cpu_only_flags_complete,
        }


class LlamaServerProcess:
    """Own one server process group, its logs, readiness, and RSS samples."""

    def __init__(
        self,
        config: LlamaServerConfig,
        *,
        log_dir: Path = Path("artifacts/runtime"),
        capabilities: LlamaServerCapabilities | None = None,
        startup_timeout_s: float = 180.0,
        readiness_interval_s: float = 0.1,
        rss_interval_s: float = 0.075,
        shutdown_timeout_s: float = 10.0,
        allow_remote: bool = False,
        strict_affinity: bool = False,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if startup_timeout_s <= 0 or shutdown_timeout_s <= 0:
            raise ValueError("startup and shutdown timeouts must be positive")
        if not allow_remote and not is_loopback_host(config.host):
            raise RemoteBindRefused(
                f"refusing non-loopback bind {config.host!r}; pass allow_remote=True explicitly"
            )
        self.config = config
        self.log_dir = Path(log_dir)
        self.command_spec = build_llama_server_command(config, capabilities)
        self.startup_timeout_s = startup_timeout_s
        self.readiness_interval_s = readiness_interval_s
        self.rss_interval_s = rss_interval_s
        self.shutdown_timeout_s = shutdown_timeout_s
        self.strict_affinity = strict_affinity
        self.environment = dict(environment or {})

        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_handle: BinaryIO | None = None
        self._stderr_handle: BinaryIO | None = None
        self._sampler: RssSampler | None = None
        self._artifacts: ProcessArtifacts | None = None
        self._health: HealthResult | None = None
        self._affinity_applied = False
        self._affinity_error: str | None = None
        self._lock = threading.RLock()
        self._atexit_registered = False
        self._old_signal_handlers: dict[int, signal.Handlers] = {}

    @property
    def health_url(self) -> str:
        host = f"[{self.config.host}]" if ":" in self.config.host else self.config.host
        return f"http://{host}:{self.config.port}/health"

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def artifacts(self) -> ProcessArtifacts:
        if self._artifacts is None:
            raise ProcessManagerError("process has not been started")
        return self._artifacts

    @property
    def command(self) -> tuple[str, ...]:
        return self.command_spec.argv

    @property
    def peak_rss_bytes(self) -> int:
        return self._sampler.peak_rss_bytes if self._sampler is not None else 0

    @property
    def ready(self) -> bool:
        return self._health is not None

    def _new_artifacts(self) -> ProcessArtifacts:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        safe_alias = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.config.model_alias).strip("-")
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        stem = f"{timestamp}-{safe_alias or 'server'}-{self.config.port}-{uuid4().hex[:8]}"
        return ProcessArtifacts(
            stdout_log=self.log_dir / f"{stem}.stdout.log",
            stderr_log=self.log_dir / f"{stem}.stderr.log",
            rss_csv=self.log_dir / f"{stem}.rss.csv",
            command_json=self.log_dir / f"{stem}.command.json",
        )

    def _apply_affinity(self) -> None:
        affinity = self.config.affinity
        process = self._process
        if not affinity or process is None:
            return
        if not hasattr(os, "sched_setaffinity"):
            self._affinity_error = "os.sched_setaffinity is unavailable on this platform"
        else:
            try:
                os.sched_setaffinity(process.pid, set(affinity))  # type: ignore[attr-defined]
                self._affinity_applied = True
            except (OSError, ValueError) as exc:
                self._affinity_error = f"{type(exc).__name__}: {exc}"
        if self._affinity_error and self.strict_affinity:
            raise ProcessManagerError(
                f"failed to apply requested CPU affinity: {self._affinity_error}"
            )

    def _write_command_record(self) -> None:
        assert self._artifacts is not None
        record = {
            "schema_version": "1.0",
            "created_at": datetime.now(UTC).isoformat(),
            "argv": list(self.command),
            "shell": False,
            "host": self.config.host,
            "port": self.config.port,
            "affinity_requested": list(self.config.affinity or ()),
            "affinity_applied": self._affinity_applied,
            "affinity_error": self._affinity_error,
            "command_proof": self.command_spec.proof.as_dict(),
        }
        self._artifacts.command_json.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def start(self, *, wait_until_ready: bool = True) -> LlamaServerProcess:
        """Launch once with ``shell=False`` and a new OS process group."""

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise ProcessManagerError("server process is already running")
            if not self.config.binary.is_file():
                raise ProcessManagerError(
                    f"llama-server binary does not exist: {self.config.binary}"
                )
            if not os.access(self.config.binary, os.X_OK):
                raise ProcessManagerError(
                    f"llama-server binary is not executable: {self.config.binary}"
                )
            if not self.config.model.is_file():
                raise ProcessManagerError(f"GGUF model file does not exist: {self.config.model}")
            if not port_is_available(self.config.host, self.config.port):
                raise PortUnavailableError(
                    f"port is already in use: {self.config.host}:{self.config.port}"
                )

            self._artifacts = self._new_artifacts()
            self._stdout_handle = self._artifacts.stdout_log.open("ab", buffering=0)
            self._stderr_handle = self._artifacts.stderr_log.open("ab", buffering=0)
            env = os.environ.copy()
            env.update(self.environment)
            try:
                self._process = subprocess.Popen(
                    list(self.command),
                    stdin=subprocess.DEVNULL,
                    stdout=self._stdout_handle,
                    stderr=self._stderr_handle,
                    env=env,
                    shell=False,
                    start_new_session=True,
                    close_fds=True,
                )
                self._apply_affinity()
                self._write_command_record()
                self._sampler = RssSampler(self._process.pid, self.rss_interval_s).start()
                atexit.register(self.stop)
                self._atexit_registered = True
                if wait_until_ready:
                    self._health = wait_for_http_ready(
                        self.health_url,
                        timeout_s=self.startup_timeout_s,
                        interval_s=self.readiness_interval_s,
                        process_poll=self._process.poll,
                    )
            except BaseException:
                self.stop()
                raise
        return self

    def install_signal_handlers(self) -> None:
        """Install main-thread SIGINT/SIGTERM cleanup for long-running serve commands."""

        if threading.current_thread() is not threading.main_thread():
            raise ProcessManagerError("signal handlers can only be installed from the main thread")
        for signum in (signal.SIGINT, signal.SIGTERM):
            if signum in self._old_signal_handlers:
                continue
            previous = signal.getsignal(signum)
            self._old_signal_handlers[signum] = previous

            def handler(received: int, frame: FrameType | None, *, old=previous) -> None:
                self.stop()
                if callable(old):
                    old(received, frame)
                elif received == signal.SIGINT:
                    raise KeyboardInterrupt
                else:
                    raise SystemExit(128 + received)

            signal.signal(signum, handler)

    def restore_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum, previous in tuple(self._old_signal_handlers.items()):
            signal.signal(signum, previous)
        self._old_signal_handlers.clear()

    def _signal_process_group(self, signum: int) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            return
        except OSError:
            process.send_signal(signum)

    def stop(self) -> None:
        """Terminate the full process group and persist final RSS samples."""

        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                self._signal_process_group(signal.SIGTERM)
                try:
                    process.wait(timeout=self.shutdown_timeout_s)
                except subprocess.TimeoutExpired:
                    self._signal_process_group(signal.SIGKILL)
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2.0)

            if self._sampler is not None:
                self._sampler.stop()
                if self._artifacts is not None:
                    self._sampler.write_csv(self._artifacts.rss_csv)
            for handle_name in ("_stdout_handle", "_stderr_handle"):
                handle = getattr(self, handle_name)
                if handle is not None and not handle.closed:
                    handle.close()
                setattr(self, handle_name, None)
            if self._atexit_registered:
                with suppress(Exception):  # pragma: no cover - interpreter shutdown edge
                    atexit.unregister(self.stop)
                self._atexit_registered = False
            self.restore_signal_handlers()

    def log_tail(self, lines: int = 30) -> str:
        if lines < 1:
            return ""
        chunks: list[str] = []
        if self._artifacts is None:
            return ""
        for label, path in (
            ("stdout", self._artifacts.stdout_log),
            ("stderr", self._artifacts.stderr_log),
        ):
            try:
                content = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            chunks.append(f"[{label}]\n" + "\n".join(content[-lines:]))
        return "\n".join(chunks)

    def snapshot(self) -> ProcessSnapshot:
        process = self._process
        returncode = process.poll() if process is not None else None
        return ProcessSnapshot(
            pid=process.pid if process is not None else None,
            running=process is not None and returncode is None,
            returncode=returncode,
            command=self.command,
            health_url=self.health_url,
            ready=self.ready,
            peak_rss_bytes=self.peak_rss_bytes,
            affinity_applied=self._affinity_applied,
            affinity_error=self._affinity_error,
            cpu_only_flags_complete=self.command_spec.proof.cpu_only_flags_complete,
        )

    def __enter__(self) -> LlamaServerProcess:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()


@contextmanager
def managed_llama_server(
    config: LlamaServerConfig,
    **manager_options: object,
) -> Iterator[LlamaServerProcess]:
    """Context manager that guarantees group cleanup on exceptions."""

    manager = LlamaServerProcess(config, **manager_options)
    try:
        yield manager.start()
    except ReadinessError as exc:
        tail = manager.log_tail()
        if tail:
            exc.add_note(f"server log tail:\n{tail}")
        raise
    finally:
        manager.stop()
