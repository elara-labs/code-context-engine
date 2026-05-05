"""Pipeline-level secret redaction wiring.

The unit-level secret detection lives in `tests/indexer/test_secrets.py`.
This file verifies the wiring through the actual pipeline:

  · `_iter_project_files` skips credential-named files when
    redact_secrets=True (default), and yields them when False.
  · The opt-out config flag (`indexer.redact_secrets: false`) propagates
    through `_iter_project_files` correctly.
"""
from __future__ import annotations


import pytest

from context_engine.indexer.pipeline import _iter_project_files, _SKIP_EXTENSIONS


@pytest.fixture
def project_with_secrets(tmp_path):
    """A project containing a mix of innocent code and credential files."""
    p = tmp_path / "proj"
    p.mkdir()
    # Innocent
    (p / "main.py").write_text("def main(): pass\n")
    (p / "README.md").write_text("# project\n")
    # Filename-level secrets — should be skipped
    (p / ".env").write_text("DB_PASSWORD=hunter2\n")
    (p / ".env.local").write_text("API_KEY=secret\n")
    (p / "credentials.json").write_text('{"key": "abc"}\n')
    # Cert-style files
    (p / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\n...\n")
    (p / "server.pem").write_text("-----BEGIN CERTIFICATE-----\n")
    return p


def _names(it):
    return sorted(p.name for p in it)


def test_iter_project_files_default_skips_secrets(project_with_secrets):
    """With redact_secrets=True (the default), credential-named files
    are excluded from the indexer's file list.
    """
    files = list(_iter_project_files(
        project_with_secrets, ignore_set=set(), skip_extensions=_SKIP_EXTENSIONS,
    ))
    names = _names(files)
    # Innocent files come through.
    assert "main.py" in names
    assert "README.md" in names
    # Secret-named files are dropped.
    assert ".env" not in names
    assert ".env.local" not in names
    assert "credentials.json" not in names
    assert "id_rsa" not in names
    assert "server.pem" not in names


def test_iter_project_files_opt_out_includes_secrets(project_with_secrets):
    """Opting out (redact_secrets=False) yields secret files too. Lets
    users on private corpora bypass the filter without forking the code.
    """
    files = list(_iter_project_files(
        project_with_secrets, ignore_set=set(), skip_extensions=_SKIP_EXTENSIONS,
        redact_secrets=False,
    ))
    names = _names(files)
    # All files come through.
    assert ".env" in names
    assert "credentials.json" in names
    assert "id_rsa" in names
    assert "server.pem" in names


def test_iter_project_files_respects_existing_ignore_set(project_with_secrets):
    """The user's `ignore` config still wins — secret-skip is additive,
    not a replacement for path-based exclusion.
    """
    # Add main.py to the ignore set; it should be excluded even though
    # it's not a secret.
    files = list(_iter_project_files(
        project_with_secrets, ignore_set={"main.py"},
        skip_extensions=_SKIP_EXTENSIONS,
    ))
    names = _names(files)
    assert "main.py" not in names
    assert "README.md" in names
