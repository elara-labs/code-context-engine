"""Tests for OpenAI Codex MCP configuration.

Codex CLI's `mcp_servers` config is read from ~/.codex/config.toml only
(user-global, not project-local). These tests pin the user-scope behavior:
detection, per-project section names, idempotency, multi-project
coexistence, legacy migration, and Windows-path TOML escaping.
"""
from __future__ import annotations

import tomllib
from unittest.mock import patch

import pytest

from context_engine.editors import (
    EDITORS,
    _project_slug,
    _toml_quote,
    configure_mcp,
    detect_editors,
    remove_mcp,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a temp dir so tests never touch the real
    ~/.codex/config.toml. HOME is what pathlib reads on POSIX; USERPROFILE
    on Windows — set both so the tests run identically on either."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def project_dir(tmp_path):
    """A separate project directory, distinct from the fake home, so the
    tests can prove writes land in ~/.codex/ rather than <project>/.codex/."""
    p = tmp_path / "project"
    p.mkdir()
    return p


# ── Schema sanity ────────────────────────────────────────────────────────────

def test_codex_editor_is_user_scoped():
    """Regression: if someone removes scope=user from the EDITORS entry, all
    the user-global tests below would still pass against a project-local
    write — because the project happens to look like a home dir to the
    code. Pin the schema invariant separately."""
    assert EDITORS["codex"]["scope"] == "user"
    assert EDITORS["codex"]["section_template"] == "mcp_servers.cce-{slug}"


# ── Slug computation ─────────────────────────────────────────────────────────

def test_slug_is_deterministic_per_path(tmp_path):
    p = tmp_path / "myapp"
    p.mkdir()
    assert _project_slug(p) == _project_slug(p)


def test_slug_differs_for_same_basename_in_different_paths(tmp_path):
    """Two projects both named "api" must get distinct slugs — otherwise
    configuring one in Codex would silently overwrite the other in the
    shared ~/.codex/config.toml."""
    a = tmp_path / "a" / "api"
    b = tmp_path / "b" / "api"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert _project_slug(a) != _project_slug(b)


def test_slug_resolves_symlinks(tmp_path):
    """Two paths pointing at the same on-disk directory (one via symlink)
    should produce the same slug — so re-running cce init via a symlinked
    path is idempotent rather than creating a duplicate Codex section."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert _project_slug(real) == _project_slug(link)


def test_slug_sanitizes_basename(tmp_path):
    """Spaces and unicode characters are not valid in TOML bare keys —
    if the slug isn't sanitized, the rendered section header is invalid TOML."""
    weird = tmp_path / "my café project (v2)"
    weird.mkdir()
    slug = _project_slug(weird)
    assert all(c.isascii() and (c.isalnum() or c in "-_") for c in slug)
    assert "é" not in slug


def test_slug_falls_back_when_basename_empty(tmp_path):
    """Edge case: a path that resolves to a directory with an empty name
    (root, etc.) should still produce a usable slug, not just `-a3f2`."""
    # Construct a real directory whose `.name` is empty by using `.` semantics
    # — actually Path("/").name is "" which is the case we want to exercise.
    from pathlib import Path
    slug = _project_slug(Path("/"))
    assert slug.startswith("project-")


# ── TOML escaping ────────────────────────────────────────────────────────────

def test_toml_quote_escapes_backslashes_and_quotes():
    assert _toml_quote(r"C:\Users\foo") == r"C:\\Users\\foo"
    assert _toml_quote('say "hi"') == r'say \"hi\"'
    assert _toml_quote("plain") == "plain"


# ── Detection ────────────────────────────────────────────────────────────────

def test_detect_codex_when_home_codex_dir_exists(fake_home, project_dir):
    """User has Codex installed (~/.codex exists) — detection should fire
    even though the project has no .codex/ marker."""
    (fake_home / ".codex").mkdir()
    detected = detect_editors(project_dir)
    assert "codex" in detected


def test_no_codex_detection_when_home_codex_absent(fake_home, project_dir):
    """User does not have Codex installed — detection must NOT fire from a
    project-local .codex/ directory (that's the bug from issue #24 in
    reverse: we shouldn't accidentally re-introduce project-local detection)."""
    (project_dir / ".codex").mkdir()
    assert "codex" not in detect_editors(project_dir)


# ── Configure: writes to ~/.codex/config.toml ────────────────────────────────

def test_configure_writes_to_user_global_codex_config(fake_home, project_dir):
    (fake_home / ".codex").mkdir()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(project_dir, "codex")
    assert changed is True
    user_config = fake_home / ".codex" / "config.toml"
    assert user_config.exists()
    # Project-local file must not be created — that was the issue #24 bug.
    assert not (project_dir / ".codex" / "config.toml").exists()


def test_configure_writes_per_project_section_name(fake_home, project_dir):
    (fake_home / ".codex").mkdir()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
    content = (fake_home / ".codex" / "config.toml").read_text()
    expected_section = f"[mcp_servers.cce-{_project_slug(project_dir)}]"
    assert expected_section in content
    # Critically NOT the legacy hardcoded form.
    assert "[mcp_servers.context-engine]" not in content


def test_configure_creates_codex_dir_if_missing(fake_home, project_dir):
    """User runs `cce init` before ever launching Codex — ~/.codex doesn't
    exist yet (so detection wouldn't fire), but if a caller invokes
    `configure_mcp("codex")` directly the dir should still be created
    rather than crashing on a missing parent."""
    assert not (fake_home / ".codex").exists()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
    assert (fake_home / ".codex" / "config.toml").exists()


# ── TOML output is parse-valid ───────────────────────────────────────────────

def test_generated_toml_parses_with_tomllib(fake_home, project_dir):
    (fake_home / ".codex").mkdir()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
    content = (fake_home / ".codex" / "config.toml").read_text()
    # Must round-trip through a real TOML parser, not just substring assertions.
    parsed = tomllib.loads(content)
    section_key = f"cce-{_project_slug(project_dir)}"
    assert section_key in parsed["mcp_servers"]
    entry = parsed["mcp_servers"][section_key]
    assert entry["command"] == "/usr/bin/cce"
    assert entry["args"] == ["serve", "--project-dir", str(project_dir)]


def test_generated_toml_handles_windows_style_path(fake_home):
    """Regression for Copilot's PR #20 review: Windows paths with
    backslashes interpolated raw into double-quoted TOML produce invalid
    output (`\\U` is a Unicode escape requiring 8 hex digits). The escape
    layer in `_codex_toml_block` must make this round-trip cleanly."""
    from context_engine.editors import _codex_toml_block
    block = _codex_toml_block(
        command=r"C:\Program Files\cce\cce.exe",
        project_dir=r"C:\Users\foo\my project",
        section="mcp_servers.cce-myproject-a3f2b1",
    )
    parsed = tomllib.loads(block)
    entry = parsed["mcp_servers"]["cce-myproject-a3f2b1"]
    assert entry["command"] == r"C:\Program Files\cce\cce.exe"
    assert entry["args"][2] == r"C:\Users\foo\my project"


# ── Idempotency + multi-project ──────────────────────────────────────────────

def test_configure_is_idempotent(fake_home, project_dir):
    (fake_home / ".codex").mkdir()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        first = configure_mcp(project_dir, "codex")
        second = configure_mcp(project_dir, "codex")
    assert first is True
    assert second is False  # second call must report "no change"
    content = (fake_home / ".codex" / "config.toml").read_text()
    # Section must appear exactly once.
    section = f"[mcp_servers.cce-{_project_slug(project_dir)}]"
    assert content.count(section) == 1


def test_multiple_projects_coexist_in_codex_config(fake_home, tmp_path):
    """Configuring two distinct projects should leave both registered in
    ~/.codex/config.toml — not have the second silently overwrite the first."""
    (fake_home / ".codex").mkdir()
    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(proj_a, "codex")
        configure_mcp(proj_b, "codex")
    parsed = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
    keys = list(parsed["mcp_servers"].keys())
    assert any(k.startswith("cce-alpha-") for k in keys)
    assert any(k.startswith("cce-beta-") for k in keys)


def test_configure_preserves_unrelated_user_config(fake_home, project_dir):
    """User has their own [mcp_servers.something-else] block in
    ~/.codex/config.toml — adding the CCE block must not touch it."""
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    user_config.write_text(
        '[mcp_servers.linear]\n'
        'command = "linear-mcp"\n'
        'args = ["--workspace", "team"]\n'
    )
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
    parsed = tomllib.loads(user_config.read_text())
    assert parsed["mcp_servers"]["linear"]["command"] == "linear-mcp"
    assert f"cce-{_project_slug(project_dir)}" in parsed["mcp_servers"]


# ── Legacy migration ─────────────────────────────────────────────────────────

def test_legacy_section_is_migrated_to_per_project(fake_home, project_dir):
    """Old code wrote [mcp_servers.context-engine] (hardcoded section). On
    first run after the fix, that legacy block should be retired in favor
    of the per-project section when it points at the same project."""
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    user_config.write_text(
        '[mcp_servers.context-engine]\n'
        'command = "/old/cce"\n'
        f'args = ["serve", "--project-dir", "{project_dir}"]\n'
    )
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
    content = user_config.read_text()
    assert "[mcp_servers.context-engine]" not in content
    assert f"[mcp_servers.cce-{_project_slug(project_dir)}]" in content


def test_legacy_section_for_different_project_is_preserved(fake_home, project_dir):
    """A user-managed legacy section for some other project should not be
    removed just because this project is being configured."""
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    user_config.write_text(
        '[mcp_servers.context-engine]\n'
        'command = "/old/cce"\n'
        'args = ["serve", "--project-dir", "/old/path"]\n'
    )
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
    content = user_config.read_text()
    assert "[mcp_servers.context-engine]" in content
    assert f"[mcp_servers.cce-{_project_slug(project_dir)}]" in content


# ── Removal: symmetrical, scoped to this project ─────────────────────────────

def test_remove_deletes_only_this_projects_section(fake_home, tmp_path):
    """`cce uninstall` in project A must NOT remove project B's CCE section
    from ~/.codex/config.toml — they share the file but should be
    independently managed."""
    (fake_home / ".codex").mkdir()
    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(proj_a, "codex")
        configure_mcp(proj_b, "codex")
        msg = remove_mcp(proj_a, "codex")
    assert msg is not None
    parsed = tomllib.loads((fake_home / ".codex" / "config.toml").read_text())
    keys = list(parsed["mcp_servers"].keys())
    assert not any(k.startswith("cce-alpha-") for k in keys), keys
    assert any(k.startswith("cce-beta-") for k in keys), keys


def test_remove_preserves_unrelated_user_sections(fake_home, project_dir):
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    user_config.write_text(
        '[mcp_servers.linear]\n'
        'command = "linear-mcp"\n'
        'args = []\n'
    )
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
        remove_mcp(project_dir, "codex")
    parsed = tomllib.loads(user_config.read_text())
    assert parsed["mcp_servers"]["linear"]["command"] == "linear-mcp"
    assert "cce-" not in str(parsed["mcp_servers"].keys())


def test_remove_returns_none_when_section_absent(fake_home, project_dir):
    """If this project was never registered, remove must be a clean no-op
    — not raise, not emit a misleading 'Removed' message."""
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    user_config.write_text('[mcp_servers.linear]\ncommand = "x"\nargs = []\n')
    msg = remove_mcp(project_dir, "codex")
    assert msg is None


def test_remove_deletes_file_when_last_section_removed(fake_home, project_dir):
    """If the file ends up empty after removing this project's section,
    delete it — we own the file iff we created it (no other entries)."""
    (fake_home / ".codex").mkdir()
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
        remove_mcp(project_dir, "codex")
    assert not (fake_home / ".codex" / "config.toml").exists()


# ── Defensive: weird filesystem state ────────────────────────────────────────

def test_configure_does_not_crash_when_codex_path_is_a_file(fake_home, project_dir):
    """If ~/.codex exists as a regular file (antivirus quarantine, user
    weirdness), configure_mcp must return None rather than blowing up
    the entire `cce init`."""
    (fake_home / ".codex").write_text("not a directory")
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        result = configure_mcp(project_dir, "codex")
    assert result is None


def test_configure_returns_none_when_codex_config_cannot_be_written(fake_home, project_dir):
    (fake_home / ".codex").mkdir()
    with (
        patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"),
        patch("context_engine.editors.atomic_write_text", side_effect=OSError),
    ):
        result = configure_mcp(project_dir, "codex")
    assert result is None


def test_configure_preserves_user_header_comment(fake_home, project_dir):
    """A user comment at the top of ~/.codex/config.toml must survive both
    configure and uninstall — earlier code stripped the whole file after
    removing a section, which silently deleted that header."""
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    user_config.write_text(
        "# my hand-written codex config\n"
        "# do not auto-edit — actually go ahead, just keep this comment\n"
        "\n"
        '[mcp_servers.linear]\n'
        'command = "linear-mcp"\n'
        'args = []\n'
    )
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        configure_mcp(project_dir, "codex")
        remove_mcp(project_dir, "codex")
    content = user_config.read_text()
    assert "# my hand-written codex config" in content
    assert "actually go ahead, just keep this comment" in content
    # Linear block is still parseable too.
    parsed = tomllib.loads(content)
    assert parsed["mcp_servers"]["linear"]["command"] == "linear-mcp"


def test_configure_rewrites_section_when_command_drifts(fake_home, project_dir):
    """A previous install wrote a section pointing at an old `cce` binary.
    On the next `cce init` (with the binary now at a new path) we must
    rewrite the section, not silently report 'already configured' and leave
    Codex pointed at a stale executable."""
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    slug = _project_slug(project_dir)
    user_config.write_text(
        f"[mcp_servers.cce-{slug}]\n"
        'command = "/old/path/to/cce"\n'
        f'args = ["serve", "--project-dir", "{project_dir}"]\n'
    )
    with patch("context_engine.editors.resolve_cce_binary", return_value="/new/path/to/cce"):
        changed = configure_mcp(project_dir, "codex")
    assert changed is True
    parsed = tomllib.loads(user_config.read_text())
    entry = parsed["mcp_servers"][f"cce-{slug}"]
    assert entry["command"] == "/new/path/to/cce"


def test_configure_rewrites_section_when_args_drift(fake_home, project_dir):
    """User (or older code) hand-edited args to something stale. cce init
    must restore the canonical args rather than reporting no-change."""
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    slug = _project_slug(project_dir)
    user_config.write_text(
        f"[mcp_servers.cce-{slug}]\n"
        'command = "/usr/bin/cce"\n'
        'args = ["serve", "--project-dir", "/some/other/path"]\n'
    )
    with patch("context_engine.editors.resolve_cce_binary", return_value="/usr/bin/cce"):
        changed = configure_mcp(project_dir, "codex")
    assert changed is True
    parsed = tomllib.loads(user_config.read_text())
    entry = parsed["mcp_servers"][f"cce-{slug}"]
    assert entry["args"] == ["serve", "--project-dir", str(project_dir)]


def test_remove_returns_none_when_codex_config_cannot_be_read(fake_home, project_dir):
    (fake_home / ".codex").mkdir()
    user_config = fake_home / ".codex" / "config.toml"
    user_config.write_text('[mcp_servers.linear]\ncommand = "x"\nargs = []\n')
    with patch("pathlib.Path.read_text", side_effect=OSError):
        result = remove_mcp(project_dir, "codex")
    assert result is None
