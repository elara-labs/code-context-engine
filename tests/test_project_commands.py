"""Tests for project commands, rules, preferences, workspace merge, and gitignore."""
import pytest
import yaml

from context_engine.project_commands import (
    load_commands,
    load_project_only,
    save_commands,
    add_command,
    add_rule,
    set_preference,
    add_custom_command,
    remove_command,
    remove_rule,
    remove_preference,
    format_for_prompt,
    ensure_gitignore,
    _merge_configs,
)


# ── load_commands ──────────────────────────────────────────────────────

class TestLoadCommands:
    def test_returns_empty_dict_when_no_file(self, tmp_path):
        assert load_commands(str(tmp_path)) == {}

    def test_returns_empty_dict_for_empty_file(self, tmp_path):
        (tmp_path / ".cce").mkdir()
        (tmp_path / ".cce" / "commands.yaml").write_text("")
        assert load_commands(str(tmp_path)) == {}

    def test_loads_valid_yaml(self, tmp_path):
        (tmp_path / ".cce").mkdir()
        (tmp_path / ".cce" / "commands.yaml").write_text(
            "before_push:\n  - composer test\n  - phpstan analyse\n"
        )
        result = load_commands(str(tmp_path))
        assert result == {"before_push": ["composer test", "phpstan analyse"]}

    def test_handles_corrupt_yaml(self, tmp_path):
        (tmp_path / ".cce").mkdir()
        (tmp_path / ".cce" / "commands.yaml").write_text(": : : invalid yaml [[[")
        assert load_commands(str(tmp_path)) == {}

    def test_handles_non_dict_yaml(self, tmp_path):
        (tmp_path / ".cce").mkdir()
        (tmp_path / ".cce" / "commands.yaml").write_text("- just a list\n")
        assert load_commands(str(tmp_path)) == {}

    def test_loads_all_sections(self, tmp_path):
        (tmp_path / ".cce").mkdir()
        data = {
            "rules": ["no down migrations"],
            "preferences": {"database": "PostgreSQL"},
            "before_push": ["test"],
            "before_commit": ["lint"],
            "on_start": ["echo hello"],
            "custom": {"deploy": "kubectl apply"},
        }
        (tmp_path / ".cce" / "commands.yaml").write_text(yaml.dump(data))
        result = load_commands(str(tmp_path))
        assert result == data


# ── Rules ──────────────────────────────────────────────────────────────

class TestRules:
    def test_add_rule(self, tmp_path):
        add_rule(str(tmp_path), "No down migrations")
        result = load_project_only(str(tmp_path))
        assert result["rules"] == ["No down migrations"]

    def test_add_multiple_rules(self, tmp_path):
        add_rule(str(tmp_path), "Rule A")
        add_rule(str(tmp_path), "Rule B")
        result = load_project_only(str(tmp_path))
        assert result["rules"] == ["Rule A", "Rule B"]

    def test_duplicate_rule_ignored(self, tmp_path):
        add_rule(str(tmp_path), "Rule A")
        add_rule(str(tmp_path), "Rule A")
        assert load_project_only(str(tmp_path))["rules"] == ["Rule A"]

    def test_remove_rule(self, tmp_path):
        add_rule(str(tmp_path), "Rule A")
        assert remove_rule(str(tmp_path), "Rule A") is True
        assert load_project_only(str(tmp_path)) == {}

    def test_remove_nonexistent_rule(self, tmp_path):
        assert remove_rule(str(tmp_path), "nope") is False

    def test_remove_preserves_other_rules(self, tmp_path):
        add_rule(str(tmp_path), "A")
        add_rule(str(tmp_path), "B")
        remove_rule(str(tmp_path), "A")
        assert load_project_only(str(tmp_path))["rules"] == ["B"]


# ── Preferences ────────────────────────────────────────────────────────

class TestPreferences:
    def test_set_preference(self, tmp_path):
        set_preference(str(tmp_path), "database", "PostgreSQL")
        result = load_project_only(str(tmp_path))
        assert result["preferences"]["database"] == "PostgreSQL"

    def test_set_multiple_preferences(self, tmp_path):
        set_preference(str(tmp_path), "database", "PostgreSQL")
        set_preference(str(tmp_path), "auth", "Sanctum")
        result = load_project_only(str(tmp_path))
        assert result["preferences"] == {"database": "PostgreSQL", "auth": "Sanctum"}

    def test_overwrite_preference(self, tmp_path):
        set_preference(str(tmp_path), "database", "MySQL")
        set_preference(str(tmp_path), "database", "PostgreSQL")
        assert load_project_only(str(tmp_path))["preferences"]["database"] == "PostgreSQL"

    def test_remove_preference(self, tmp_path):
        set_preference(str(tmp_path), "database", "PostgreSQL")
        assert remove_preference(str(tmp_path), "database") is True
        assert load_project_only(str(tmp_path)) == {}

    def test_remove_nonexistent_preference(self, tmp_path):
        assert remove_preference(str(tmp_path), "nope") is False


# ── Commands (existing tests updated) ─────────────────────────────────

class TestAddCommand:
    def test_add_to_empty_project(self, tmp_path):
        add_command(str(tmp_path), "before_push", "composer test")
        result = load_project_only(str(tmp_path))
        assert result == {"before_push": ["composer test"]}

    def test_add_multiple_commands(self, tmp_path):
        add_command(str(tmp_path), "before_push", "composer test")
        add_command(str(tmp_path), "before_push", "phpstan analyse")
        result = load_project_only(str(tmp_path))
        assert result["before_push"] == ["composer test", "phpstan analyse"]

    def test_duplicate_command_ignored(self, tmp_path):
        add_command(str(tmp_path), "before_push", "composer test")
        add_command(str(tmp_path), "before_push", "composer test")
        assert load_project_only(str(tmp_path))["before_push"] == ["composer test"]

    def test_invalid_hook_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid hook"):
            add_command(str(tmp_path), "invalid_hook", "test")

    def test_custom_hook_raises(self, tmp_path):
        with pytest.raises(ValueError, match="add_custom_command"):
            add_command(str(tmp_path), "custom", "test")


class TestCustomCommand:
    def test_add_custom(self, tmp_path):
        add_custom_command(str(tmp_path), "deploy", "kubectl apply")
        assert load_project_only(str(tmp_path))["custom"]["deploy"] == "kubectl apply"

    def test_overwrite_custom(self, tmp_path):
        add_custom_command(str(tmp_path), "deploy", "v1")
        add_custom_command(str(tmp_path), "deploy", "v2")
        assert load_project_only(str(tmp_path))["custom"]["deploy"] == "v2"


class TestRemoveCommand:
    def test_remove_existing(self, tmp_path):
        add_command(str(tmp_path), "before_push", "test")
        assert remove_command(str(tmp_path), "before_push", "test") is True
        assert load_project_only(str(tmp_path)) == {}

    def test_remove_nonexistent(self, tmp_path):
        assert remove_command(str(tmp_path), "before_push", "test") is False

    def test_remove_custom(self, tmp_path):
        add_custom_command(str(tmp_path), "deploy", "go")
        assert remove_command(str(tmp_path), "custom", "deploy") is True
        assert load_project_only(str(tmp_path)) == {}

    def test_remove_preserves_other(self, tmp_path):
        add_command(str(tmp_path), "before_push", "a")
        add_command(str(tmp_path), "before_push", "b")
        remove_command(str(tmp_path), "before_push", "a")
        assert load_project_only(str(tmp_path)) == {"before_push": ["b"]}


# ── Workspace merge ───────────────────────────────────────────────────

class TestWorkspaceMerge:
    def _setup_workspace(self, tmp_path, ws_data, proj_data=None):
        """Create workspace/.cce + workspace/project-a/.cce"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".cce").mkdir()
        (workspace / ".cce" / "commands.yaml").write_text(yaml.dump(ws_data))
        project = workspace / "project-a"
        project.mkdir()
        if proj_data:
            (project / ".cce").mkdir()
            (project / ".cce" / "commands.yaml").write_text(yaml.dump(proj_data))
        return workspace, project

    def test_workspace_only(self, tmp_path):
        ws, proj = self._setup_workspace(tmp_path, {"rules": ["PSR-12"]})
        result = load_commands(str(proj))
        assert result["rules"] == ["PSR-12"]

    def test_project_only_no_workspace(self, tmp_path):
        proj = tmp_path / "standalone"
        proj.mkdir()
        (proj / ".cce").mkdir()
        (proj / ".cce" / "commands.yaml").write_text(yaml.dump({"rules": ["my rule"]}))
        result = load_commands(str(proj))
        assert result["rules"] == ["my rule"]

    def test_merge_rules(self, tmp_path):
        ws, proj = self._setup_workspace(
            tmp_path,
            ws_data={"rules": ["Global rule"]},
            proj_data={"rules": ["Project rule"]},
        )
        result = load_commands(str(proj))
        assert result["rules"] == ["Global rule", "Project rule"]

    def test_merge_rules_dedup(self, tmp_path):
        ws, proj = self._setup_workspace(
            tmp_path,
            ws_data={"rules": ["Same rule"]},
            proj_data={"rules": ["Same rule", "Extra"]},
        )
        result = load_commands(str(proj))
        assert result["rules"] == ["Same rule", "Extra"]

    def test_merge_preferences(self, tmp_path):
        ws, proj = self._setup_workspace(
            tmp_path,
            ws_data={"preferences": {"language": "PHP", "framework": "Laravel"}},
            proj_data={"preferences": {"database": "PostgreSQL", "framework": "Lumen"}},
        )
        result = load_commands(str(proj))
        assert result["preferences"]["language"] == "PHP"
        assert result["preferences"]["database"] == "PostgreSQL"
        assert result["preferences"]["framework"] == "Lumen"  # project wins

    def test_merge_commands(self, tmp_path):
        ws, proj = self._setup_workspace(
            tmp_path,
            ws_data={"before_push": ["global test"]},
            proj_data={"before_push": ["project test"]},
        )
        result = load_commands(str(proj))
        assert result["before_push"] == ["global test", "project test"]

    def test_merge_custom(self, tmp_path):
        ws, proj = self._setup_workspace(
            tmp_path,
            ws_data={"custom": {"lint": "global lint"}},
            proj_data={"custom": {"deploy": "project deploy"}},
        )
        result = load_commands(str(proj))
        assert result["custom"]["lint"] == "global lint"
        assert result["custom"]["deploy"] == "project deploy"

    def test_no_workspace_file(self, tmp_path):
        """Project in subdirectory but no workspace .cce — should still work."""
        parent = tmp_path / "parent"
        project = parent / "project"
        project.mkdir(parents=True)
        add_rule(str(project), "my rule")
        result = load_commands(str(project))
        assert result["rules"] == ["my rule"]

    def test_workspace_empty_project_full(self, tmp_path):
        ws, proj = self._setup_workspace(
            tmp_path,
            ws_data={},
            proj_data={"rules": ["project rule"], "preferences": {"db": "pg"}},
        )
        result = load_commands(str(proj))
        assert result["rules"] == ["project rule"]
        assert result["preferences"]["db"] == "pg"


# ── _merge_configs unit tests ─────────────────────────────────────────

class TestMergeConfigs:
    def test_empty_both(self):
        assert _merge_configs({}, {}) == {}

    def test_workspace_only(self):
        result = _merge_configs({"rules": ["a"]}, {})
        assert result == {"rules": ["a"]}

    def test_project_only(self):
        result = _merge_configs({}, {"rules": ["b"]})
        assert result == {"rules": ["b"]}

    def test_list_merge_dedup(self):
        result = _merge_configs({"rules": ["a", "b"]}, {"rules": ["b", "c"]})
        assert result == {"rules": ["a", "b", "c"]}

    def test_dict_merge_project_wins(self):
        result = _merge_configs(
            {"preferences": {"x": "1", "y": "2"}},
            {"preferences": {"y": "3", "z": "4"}},
        )
        assert result == {"preferences": {"x": "1", "y": "3", "z": "4"}}

    def test_mixed_sections(self):
        result = _merge_configs(
            {"rules": ["global"], "preferences": {"lang": "PHP"}, "before_push": ["test"]},
            {"rules": ["local"], "custom": {"deploy": "go"}},
        )
        assert result["rules"] == ["global", "local"]
        assert result["preferences"] == {"lang": "PHP"}
        assert result["before_push"] == ["test"]
        assert result["custom"] == {"deploy": "go"}


# ── format_for_prompt ──────────────────────────────────────────────────

class TestFormatForPrompt:
    def test_empty(self):
        assert format_for_prompt({}) == ""

    def test_rules_only(self):
        result = format_for_prompt({"rules": ["No down migrations"]})
        assert "### Project Rules" in result
        assert "No down migrations" in result

    def test_preferences_only(self):
        result = format_for_prompt({"preferences": {"database": "PostgreSQL"}})
        assert "### Project Preferences" in result
        assert "**database:** PostgreSQL" in result

    def test_commands_only(self):
        result = format_for_prompt({"before_push": ["composer test"]})
        assert "### Project Commands" in result
        assert "`composer test`" in result

    def test_all_sections(self):
        result = format_for_prompt({
            "rules": ["rule 1"],
            "preferences": {"db": "pg"},
            "before_push": ["test"],
            "custom": {"deploy": "go"},
        })
        assert "Rules" in result
        assert "Preferences" in result
        assert "Commands" in result

    def test_custom_label(self):
        result = format_for_prompt({"rules": ["x"]}, label="Workspace")
        assert "### Workspace Rules" in result

    def test_ignores_invalid_types(self):
        result = format_for_prompt({"rules": "not a list", "preferences": "not a dict"})
        assert result == ""


# ── ensure_gitignore ──────────────────────────────────────────────────

class TestEnsureGitignore:
    def test_creates_gitignore(self, tmp_path):
        ensure_gitignore(str(tmp_path))
        content = (tmp_path / ".gitignore").read_text()
        assert ".cce/" in content

    def test_appends_to_existing(self, tmp_path):
        (tmp_path / ".gitignore").write_text("node_modules/\n.env\n")
        ensure_gitignore(str(tmp_path))
        content = (tmp_path / ".gitignore").read_text()
        assert "node_modules/" in content
        assert ".env" in content
        assert ".cce/" in content

    def test_idempotent(self, tmp_path):
        ensure_gitignore(str(tmp_path))
        ensure_gitignore(str(tmp_path))
        content = (tmp_path / ".gitignore").read_text()
        assert content.count(".cce/") == 1

    def test_doesnt_duplicate_if_present(self, tmp_path):
        (tmp_path / ".gitignore").write_text("stuff\n.cce/\nmore\n")
        ensure_gitignore(str(tmp_path))
        content = (tmp_path / ".gitignore").read_text()
        assert content.count(".cce/") == 1


# ── Edge cases ─────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_special_characters_in_rule(self, tmp_path):
        add_rule(str(tmp_path), "Use `strict_types` in PHP — always!")
        result = load_project_only(str(tmp_path))
        assert "strict_types" in result["rules"][0]

    def test_long_preference_value(self, tmp_path):
        set_preference(str(tmp_path), "style", "x" * 5000)
        result = load_project_only(str(tmp_path))
        assert len(result["preferences"]["style"]) == 5000

    def test_mixed_rules_prefs_commands(self, tmp_path):
        add_rule(str(tmp_path), "my rule")
        set_preference(str(tmp_path), "db", "pg")
        add_command(str(tmp_path), "before_push", "test")
        add_custom_command(str(tmp_path), "deploy", "go")
        result = load_project_only(str(tmp_path))
        assert result["rules"] == ["my rule"]
        assert result["preferences"]["db"] == "pg"
        assert result["before_push"] == ["test"]
        assert result["custom"]["deploy"] == "go"

    def test_save_load_roundtrip_all_sections(self, tmp_path):
        data = {
            "rules": ["a", "b"],
            "preferences": {"x": "1", "y": "2"},
            "before_push": ["test"],
            "before_commit": ["lint"],
            "on_start": ["hi"],
            "custom": {"deploy": "go"},
        }
        save_commands(str(tmp_path), data)
        assert load_project_only(str(tmp_path)) == data
