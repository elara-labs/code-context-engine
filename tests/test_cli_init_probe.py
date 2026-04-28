"""Tests for the cce init reachability probe (`_check_memory_capture_reachable`).

Hooks fail closed (`curl ... || true`) so capture is silently dropped when
`cce serve` isn't running. The probe tells the user what state they're in
right after init, instead of letting them discover it by surprise.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from context_engine.cli import _check_memory_capture_reachable
from context_engine.config import Config


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    config = Config(storage_path=str(tmp_path / "storage"))
    storage_base = tmp_path / "storage" / "proj"
    storage_base.mkdir(parents=True)
    return config, project, storage_base


def _capture(callable_):
    runner = CliRunner()
    with runner.isolation():
        callable_()
    # `runner.isolation()` swallows output by default; use `runner.invoke`
    # via a tiny click command instead.
    import click
    out = []

    @click.command()
    def _wrap():
        callable_()

    result = runner.invoke(_wrap)
    return result.output


def test_probe_warns_when_port_file_missing(setup):
    config, project, storage_base = setup
    text = _capture(lambda: _check_memory_capture_reachable(config, project))
    assert "not yet active" in text
    assert "cce serve" in text


def test_probe_warns_when_port_is_stale(setup):
    config, project, storage_base = setup
    # Port that's almost certainly closed.
    (storage_base / "serve.port").write_text("9")
    text = _capture(lambda: _check_memory_capture_reachable(config, project))
    assert "stale" in text


def test_probe_confirms_when_port_is_listening(setup):
    config, project, storage_base = setup
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    real_port = s.getsockname()[1]
    try:
        (storage_base / "serve.port").write_text(str(real_port))
        text = _capture(lambda: _check_memory_capture_reachable(config, project))
        assert "active" in text
        assert str(real_port) in text
    finally:
        s.close()


def test_probe_warns_on_unparsable_port_file(setup):
    config, project, storage_base = setup
    (storage_base / "serve.port").write_text("not-a-port")
    text = _capture(lambda: _check_memory_capture_reachable(config, project))
    assert "unreadable" in text


def test_probe_falls_back_to_default_rendezvous_when_storage_local_missing(
    setup, tmp_path, monkeypatch,
):
    """When storage_path is customised, hook_server writes the port to BOTH
    the storage-local path AND the default-path rendezvous
    (~/.cce/projects/<name>/serve.port). The probe must succeed when only
    the rendezvous file is present (storage-local missing)."""
    config, project, _ = setup
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    rendezvous = fake_home / ".cce" / "projects" / project.name / "serve.port"
    rendezvous.parent.mkdir(parents=True)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    real_port = s.getsockname()[1]
    try:
        rendezvous.write_text(str(real_port))
        text = _capture(lambda: _check_memory_capture_reachable(config, project))
        assert "active" in text
        assert str(real_port) in text
    finally:
        s.close()
