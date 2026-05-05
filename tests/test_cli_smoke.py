"""Smoke tests for all `cce` CLI commands.

Every public subcommand gets at least one test that verifies it exits
cleanly (exit_code == 0) and produces expected output. These run fast
with mocked storage so CI catches regressions without a real index.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from context_engine.cli import main
from context_engine.config import Config


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def storage(tmp_path):
    """Set up a fake storage root with one project that has stats."""
    project = tmp_path / "test-project"
    project.mkdir()
    (project / "stats.json").write_text(json.dumps({
        "queries": 5,
        "full_file_tokens": 20000,
        "raw_tokens": 8000,
        "served_tokens": 2000,
    }))
    # Minimal vectors dir so status doesn't complain
    (project / "vectors").mkdir()
    return tmp_path


def _patch_config(storage_path: str, project_name: str = "test-project"):
    """Return context managers that patch config and cwd."""
    config = Config(storage_path=storage_path)
    cwd = Path(storage_path) / ".." / project_name
    cwd = cwd.resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    return (
        patch("context_engine.cli.load_config", return_value=config),
        patch("context_engine.cli.Path.cwd", return_value=cwd),
    )


# ── Banner (no subcommand) ──────────────────────────────────


def test_banner(runner, storage):
    """cce with no subcommand shows the welcome banner."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert "C C E" in result.output
    assert "Code Context Engine" in result.output


# ── Version ─────────────────────────────────────────────────


def test_version(runner):
    """--version prints version string."""
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output.lower()


# ── List ────────────────────────────────────────────────────


def test_list(runner, storage):
    """cce list shows available commands."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "cce init" in result.output
    assert "cce status" in result.output
    assert "cce savings" in result.output


# ── Status ──────────────────────────────────────────────────


def test_status(runner, storage):
    """cce status shows storage and config info."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    out = result.output.lower()
    assert "storage" in out or "status" in out


def test_status_json(runner, storage):
    """cce status --json returns valid JSON."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "storage_path" in data


# ── Savings ─────────────────────────────────────────────────


def test_savings(runner, storage):
    """cce savings shows token savings report."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    assert "test-project" in result.output
    assert "tokens saved" in result.output.lower() or "%" in result.output


def test_savings_json(runner, storage):
    """cce savings --json returns valid JSON with required fields."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    for key in ["project", "queries", "tokens_saved", "savings_pct"]:
        assert key in data, f"missing key: {key}"


def test_savings_all(runner, storage):
    """cce savings --all lists all projects."""
    # Add a second project
    p2_dir = storage / "proj-two"
    p2_dir.mkdir()
    (p2_dir / "stats.json").write_text(json.dumps({
        "queries": 2, "full_file_tokens": 5000,
        "raw_tokens": 3000, "served_tokens": 1000,
    }))
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings", "--all"])
    assert result.exit_code == 0
    assert "test-project" in result.output
    assert "proj-two" in result.output
    assert "Total" in result.output


def test_savings_all_json(runner, storage):
    """cce savings --all --json returns projects array."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings", "--all", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "projects" in data
    assert len(data["projects"]) >= 1


def test_savings_no_data(runner, tmp_path):
    """cce savings with no stats shows empty message."""
    p1, p2 = _patch_config(str(tmp_path), "empty-project")
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    assert "No usage recorded" in result.output


def test_savings_bucket_fallback_from_stats_json(runner, tmp_path):
    """When memory.db has no savings rows, buckets fall back to stats.json."""
    project = tmp_path / "bucket-test"
    project.mkdir()
    (project / "stats.json").write_text(json.dumps({
        "queries": 3,
        "raw_tokens": 0,
        "served_tokens": 0,
        "full_file_tokens": 0,
        "buckets": {
            "output_compression": {"baseline": 1500, "served": 525, "calls": 3},
        },
    }))
    p1, p2 = _patch_config(str(tmp_path), "bucket-test")
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    assert "65%" in result.output
    assert "bucket-test" in result.output


# ── Commands ────────────────────────────────────────────────


def test_commands_list_empty(runner, storage):
    """cce commands list works when no project config exists."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["commands", "list"])
    assert result.exit_code == 0


# ── Prune ───────────────────────────────────────────────────


def test_prune_dry_run(runner, storage):
    """cce prune --dry-run completes without deleting."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["prune", "--dry-run"])
    assert result.exit_code == 0
    assert "prune" in result.output.lower() or "nothing" in result.output.lower()


# ── Services ────────────────────────────────────────────────


def test_services(runner, storage):
    """cce services shows service status."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["services"])
    assert result.exit_code == 0
    out = result.output.lower()
    assert "ollama" in out
    assert "dashboard" in out


# ── Clear ───────────────────────────────────────────────────


def test_clear_no_data(runner, tmp_path):
    """cce clear on empty project reports nothing to clear."""
    p1, p2 = _patch_config(str(tmp_path), "no-data")
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["clear"])
    assert result.exit_code == 0
    assert "no index data" in result.output.lower() or "not found" in result.output.lower()


# ── Dynamic pricing ─────────────────────────────────────────


def test_pricing_fetch_and_fallback():
    """get_model_pricing returns a dict with opus/sonnet/haiku keys."""
    from context_engine.pricing import get_model_pricing
    pricing = get_model_pricing()
    assert isinstance(pricing, dict)
    assert len(pricing) >= 1
    # All values are positive floats
    for family, price in pricing.items():
        assert isinstance(price, (int, float))
        assert price > 0


def test_pricing_fallback_on_network_error():
    """When fetch fails, fallback pricing is returned."""
    from context_engine.pricing import get_model_pricing, _FALLBACK, _CACHE_PATH
    # Clear cache so it tries to fetch
    if _CACHE_PATH.exists():
        _CACHE_PATH.unlink()
    with patch("context_engine.pricing._fetch", return_value=None):
        pricing = get_model_pricing()
    assert pricing == _FALLBACK


def test_pricing_shown_in_savings_output(runner, storage):
    """Savings report shows cost estimate line with model name."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    assert "Cost estimate" in result.output
    assert "/1M tokens" in result.output


# ── Grid bar rendering ──────────────────────────────────────


def test_grid_bar_min_one_filled(runner, storage):
    """Even at 98% savings, at least one ⛁ cell shows."""
    p1, p2 = _patch_config(str(storage))
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    assert "⛁" in result.output


def test_grid_bar_high_usage(runner, tmp_path):
    """Low savings shows more ⛁ cells."""
    project = tmp_path / "low-savings"
    project.mkdir()
    (project / "stats.json").write_text(json.dumps({
        "queries": 2, "full_file_tokens": 1000,
        "raw_tokens": 1000, "served_tokens": 800,
    }))
    p1, p2 = _patch_config(str(tmp_path), "low-savings")
    with runner.isolated_filesystem(), p1, p2:
        result = runner.invoke(main, ["savings"])
    assert result.exit_code == 0
    # 20% savings = 80% usage = 8 filled cells
    assert result.output.count("⛁") >= 7
