from __future__ import annotations

import json
import os
import socket
import stat
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from a64pilot.runtime.llama_command import (
    CommandConfigurationError,
    LlamaServerCapabilities,
    LlamaServerConfig,
    build_llama_server_command,
)
from a64pilot.runtime.process_manager import (
    LlamaServerProcess,
    PortUnavailableError,
    RemoteBindRefused,
    find_available_port,
)

FAKE_SERVER = r"""#!/usr/bin/env python3
import json
import os
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

argv = os.sys.argv
def value(option):
    return argv[argv.index(option) + 1]

host = value("--host")
port = int(value("--port"))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, fmt, *args):
        print(fmt % args, file=os.sys.stderr, flush=True)

server = ThreadingHTTPServer((host, port), Handler)
signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
print("fake llama-server CPU only", flush=True)
server.serve_forever()
"""


@pytest.fixture
def fake_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "fake llama-server"
    binary.write_text(FAKE_SERVER, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def make_config(fake_binary: Path, tmp_path: Path, **overrides: object) -> LlamaServerConfig:
    model = tmp_path / "model file.gguf"
    model.touch(exist_ok=True)
    defaults: dict[str, object] = {
        "binary": fake_binary,
        "model": model,
        "port": find_available_port(),
        "threads": 4,
        "batch_size": 256,
        "ubatch_size": 128,
    }
    defaults.update(overrides)
    return LlamaServerConfig(**defaults)  # type: ignore[arg-type]


def test_typed_command_is_shell_free_and_cpu_only(fake_binary: Path, tmp_path: Path) -> None:
    config = make_config(fake_binary, tmp_path)
    command = build_llama_server_command(config)
    argv = command.as_list()
    assert argv[0] == str(fake_binary)
    assert argv[argv.index("--model") + 1] == str(tmp_path / "model file.gguf")
    assert argv[argv.index("--device") + 1] == "none"
    assert argv[argv.index("--n-gpu-layers") + 1] == "0"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv.count("-lv") == 1
    assert argv[argv.index("-lv") + 1] == "5"
    assert command.proof.cpu_only_flags_complete
    assert command.proof.localhost


def test_port_allocator_does_not_reuse_between_candidates() -> None:
    first = find_available_port(23000)
    second = find_available_port(23000)
    assert second != first


def test_cpu_only_command_refuses_incomplete_pinned_capabilities(
    fake_binary: Path, tmp_path: Path
) -> None:
    config = make_config(fake_binary, tmp_path)
    caps = LlamaServerCapabilities(device=False, gpu_layers=True)
    with pytest.raises(CommandConfigurationError, match="missing --device"):
        build_llama_server_command(config, caps)


@pytest.mark.parametrize(
    "extra",
    [
        ("--device", "cuda"),
        ("--n-gpu-layers=99",),
        ("--model", "other.gguf"),
        ("--port=9999",),
        ("--seed", "7"),
        ("--alias=misleading",),
        ("-lv", "3"),
        ("--verbosity=3",),
        ("--log-verbosity", "5"),
    ],
)
def test_protected_options_cannot_be_overridden(
    fake_binary: Path, tmp_path: Path, extra: tuple[str, ...]
) -> None:
    with pytest.raises(CommandConfigurationError):
        make_config(fake_binary, tmp_path, extra_args=extra)


def test_ubatch_must_not_exceed_batch(fake_binary: Path, tmp_path: Path) -> None:
    with pytest.raises(CommandConfigurationError, match="ubatch_size"):
        make_config(fake_binary, tmp_path, batch_size=64, ubatch_size=128)


@pytest.mark.parametrize("verbosity", [-1, 6])
def test_log_verbosity_is_bounded(fake_binary: Path, tmp_path: Path, verbosity: int) -> None:
    with pytest.raises(CommandConfigurationError, match="log_verbosity"):
        make_config(fake_binary, tmp_path, log_verbosity=verbosity)


def test_remote_bind_requires_explicit_opt_in(fake_binary: Path, tmp_path: Path) -> None:
    config = make_config(fake_binary, tmp_path, host="0.0.0.0")
    with pytest.raises(RemoteBindRefused):
        LlamaServerProcess(config)


def test_missing_model_fails_before_launch(fake_binary: Path, tmp_path: Path) -> None:
    missing = tmp_path / "missing.gguf"
    manager = LlamaServerProcess(make_config(fake_binary, tmp_path, model=missing))
    with pytest.raises(RuntimeError, match="GGUF model file does not exist"):
        manager.start()
    assert manager.process is None


def test_nonexecutable_binary_fails_before_launch(fake_binary: Path, tmp_path: Path) -> None:
    fake_binary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    manager = LlamaServerProcess(make_config(fake_binary, tmp_path))
    with pytest.raises(RuntimeError, match="not executable"):
        manager.start()
    assert manager.process is None


def test_process_readiness_logs_rss_and_cleanup(fake_binary: Path, tmp_path: Path) -> None:
    config = make_config(fake_binary, tmp_path)
    manager = LlamaServerProcess(
        config,
        log_dir=tmp_path / "logs",
        startup_timeout_s=5,
        readiness_interval_s=0.05,
        shutdown_timeout_s=2,
    )
    with manager:
        assert manager.ready
        assert manager.pid is not None
        assert manager.process is not None and manager.process.poll() is None
        with urlopen(manager.health_url, timeout=1) as response:  # noqa: S310 - local test endpoint
            assert json.load(response)["status"] == "ok"
        snapshot = manager.snapshot()
        assert snapshot.running
        assert snapshot.cpu_only_flags_complete
        time.sleep(0.1)

    assert manager.process is not None
    assert manager.process.poll() is not None
    assert manager.artifacts.stdout_log.exists()
    assert "fake llama-server CPU only" in manager.artifacts.stdout_log.read_text(encoding="utf-8")
    assert manager.artifacts.stderr_log.exists()
    assert manager.artifacts.rss_csv.exists()
    command_record = json.loads(manager.artifacts.command_json.read_text(encoding="utf-8"))
    assert command_record["shell"] is False
    assert command_record["command_proof"]["device_none"] is True
    assert command_record["command_proof"]["gpu_layers_zero"] is True
    assert command_record["argv"].count("-lv") == 1
    assert command_record["argv"][command_record["argv"].index("-lv") + 1] == "5"
    assert os.path.basename(command_record["argv"][0]) == "fake llama-server"


def test_complete_log_snapshot_survives_tail_rollover(fake_binary: Path, tmp_path: Path) -> None:
    manager = LlamaServerProcess(
        make_config(fake_binary, tmp_path),
        log_dir=tmp_path / "logs",
        startup_timeout_s=5,
    )
    with manager:
        assert manager.ready
    marker = "kleidiai: primary q4 kernel feature DOTPROD"
    manager.artifacts.stderr_log.write_text(
        marker + "\n" + "\n".join(f"debug line {index}" for index in range(4000)) + "\n",
        encoding="utf-8",
    )

    assert marker not in manager.log_tail(3000)
    assert marker in manager.log_text()


def test_exception_inside_context_still_stops_process(fake_binary: Path, tmp_path: Path) -> None:
    manager = LlamaServerProcess(
        make_config(fake_binary, tmp_path),
        log_dir=tmp_path / "logs",
        startup_timeout_s=5,
    )
    with pytest.raises(RuntimeError, match="boom"), manager:
        raise RuntimeError("boom")
    assert manager.process is not None and manager.process.poll() is not None


def test_occupied_port_fails_before_launch(fake_binary: Path, tmp_path: Path) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    try:
        manager = LlamaServerProcess(make_config(fake_binary, tmp_path, port=port))
        with pytest.raises(PortUnavailableError):
            manager.start()
    finally:
        sock.close()
