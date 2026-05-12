"""Project-specific commands, rules, and preferences loaded at session start.

Supports two levels:
- **Workspace** (optional): parent directory's .cce/commands.yaml — global
  defaults that apply to all projects under it.
- **Project**: the project's own .cce/commands.yaml — extends or overrides
  the workspace config.

Example .cce/commands.yaml:
    rules:
      - NEVER generate down() in migrations — forward-only
      - Use UUID for primary keys
    preferences:
      database: PostgreSQL
      auth: Sanctum
      style: "Clean architecture"
    before_push:
      - composer test
      - phpstan analyse
    before_commit:
      - php-cs-fixer fix --dry-run
    on_start:
      - echo "Deploy freeze until Friday"
    custom:
      deploy: kubectl apply -f k8s/
"""
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

COMMANDS_DIR = ".cce"
COMMANDS_FILE = "commands.yaml"

VALID_HOOKS = {"before_push", "before_commit", "on_start", "custom"}
# Sections that are lists (merged by appending, deduped)
_LIST_SECTIONS = {"rules", "before_push", "before_commit", "on_start"}
# Sections that are dicts (merged by update)
_DICT_SECTIONS = {"preferences", "custom"}


def _commands_path(project_dir: str) -> Path:
    return Path(project_dir) / COMMANDS_DIR / COMMANDS_FILE


def _load_yaml(path: Path) -> dict:
    """Load a YAML file. Returns {} on any error."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError) as exc:
        log.warning("Failed to parse %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("%s is not a valid YAML mapping", path)
        return {}
    return data


def _find_workspace_dir(project_dir: str) -> Path | None:
    """Find the nearest parent with .cce/commands.yaml (not project_dir itself)."""
    current = Path(project_dir).resolve().parent
    home = Path.home()
    # Walk up but stop at home directory (don't scan /Users or /)
    while current != current.parent and current != home.parent:
        candidate = current / COMMANDS_DIR / COMMANDS_FILE
        if candidate.exists():
            return current
        current = current.parent
    return None


def _merge_configs(workspace: dict, project: dict) -> dict:
    """Merge workspace config into project config. Project wins on conflicts."""
    merged = {}
    all_keys = set(workspace.keys()) | set(project.keys())
    for key in all_keys:
        ws_val = workspace.get(key)
        pj_val = project.get(key)
        if key in _LIST_SECTIONS:
            # Merge lists, project items come after workspace, deduplicate
            ws_list = ws_val if isinstance(ws_val, list) else []
            pj_list = pj_val if isinstance(pj_val, list) else []
            merged_list = []
            seen_strs: set[str] = set()
            for item in ws_list + pj_list:
                item_key = str(item)
                if item_key not in seen_strs:
                    seen_strs.add(item_key)
                    merged_list.append(item)
            if merged_list:
                merged[key] = merged_list
        elif key in _DICT_SECTIONS:
            # Merge dicts, project overrides workspace
            ws_dict = ws_val if isinstance(ws_val, dict) else {}
            pj_dict = pj_val if isinstance(pj_val, dict) else {}
            combined = {**ws_dict, **pj_dict}
            if combined:
                merged[key] = combined
        else:
            # Unknown section: project wins, fallback to workspace
            merged[key] = pj_val if pj_val is not None else ws_val
    return merged


def load_commands(project_dir: str) -> dict:
    """Load merged config: workspace (optional) + project."""
    project_config = _load_yaml(_commands_path(project_dir))
    workspace_dir = _find_workspace_dir(project_dir)
    if workspace_dir is None:
        return project_config
    workspace_config = _load_yaml(workspace_dir / COMMANDS_DIR / COMMANDS_FILE)
    return _merge_configs(workspace_config, project_config)


def load_project_only(project_dir: str) -> dict:
    """Load only the project-level config (no workspace merge)."""
    return _load_yaml(_commands_path(project_dir))


def save_commands(project_dir: str, commands: dict) -> None:
    """Save project commands to .cce/commands.yaml."""
    path = _commands_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(commands, default_flow_style=False, sort_keys=False))


def add_command(project_dir: str, hook: str, command: str) -> None:
    """Add a command to a hook. Creates the file if it doesn't exist."""
    if hook not in VALID_HOOKS:
        raise ValueError(f"Invalid hook '{hook}'. Valid hooks: {', '.join(sorted(VALID_HOOKS))}")
    if hook == "custom":
        raise ValueError("Use add_custom_command() for custom commands")
    commands = load_project_only(project_dir)
    hook_list = commands.setdefault(hook, [])
    if not isinstance(hook_list, list):
        raise ValueError(f"Hook '{hook}' is not a list in commands.yaml")
    if command in hook_list:
        return
    hook_list.append(command)
    save_commands(project_dir, commands)


def add_rule(project_dir: str, rule: str) -> None:
    """Add a rule. Creates the file if it doesn't exist."""
    commands = load_project_only(project_dir)
    rules = commands.setdefault("rules", [])
    if not isinstance(rules, list):
        raise ValueError("'rules' section must be a list in commands.yaml")
    if rule in rules:
        return
    rules.append(rule)
    save_commands(project_dir, commands)


def set_preference(project_dir: str, key: str, value: str) -> None:
    """Set a preference key-value pair."""
    commands = load_project_only(project_dir)
    prefs = commands.setdefault("preferences", {})
    if not isinstance(prefs, dict):
        raise ValueError("'preferences' section must be a mapping in commands.yaml")
    prefs[key] = value
    save_commands(project_dir, commands)


def add_custom_command(project_dir: str, name: str, command: str) -> None:
    """Add a named custom command."""
    commands = load_project_only(project_dir)
    custom = commands.setdefault("custom", {})
    if not isinstance(custom, dict):
        raise ValueError("'custom' section must be a mapping in commands.yaml")
    custom[name] = command
    save_commands(project_dir, commands)


def remove_command(project_dir: str, hook: str, command: str) -> bool:
    """Remove a command from a hook. Returns True if removed."""
    commands = load_project_only(project_dir)
    if hook not in commands:
        return False
    if hook == "custom":
        custom = commands.get("custom", {})
        if command in custom:
            del custom[command]
            if not custom:
                del commands["custom"]
            save_commands(project_dir, commands)
            return True
        return False
    hook_list = commands.get(hook, [])
    if not isinstance(hook_list, list):
        return False
    if command in hook_list:
        hook_list.remove(command)
        if not hook_list:
            del commands[hook]
        save_commands(project_dir, commands)
        return True
    return False


def remove_rule(project_dir: str, rule: str) -> bool:
    """Remove a rule. Returns True if removed."""
    commands = load_project_only(project_dir)
    rules = commands.get("rules", [])
    if not isinstance(rules, list) or rule not in rules:
        return False
    rules.remove(rule)
    if not rules:
        del commands["rules"]
    save_commands(project_dir, commands)
    return True


def remove_preference(project_dir: str, key: str) -> bool:
    """Remove a preference. Returns True if removed."""
    commands = load_project_only(project_dir)
    prefs = commands.get("preferences", {})
    if not isinstance(prefs, dict) or key not in prefs:
        return False
    del prefs[key]
    if not prefs:
        del commands["preferences"]
    save_commands(project_dir, commands)
    return True


_GITIGNORE_ENTRIES = [
    # CCE local cache and per-machine files
    (".cce/", "CCE local cache (per-machine, not for version control)"),
    (".claude/settings.local.json", "Claude Code local settings written by cce init"),
    # `.mcp.json` carries an absolute path to the `cce` binary (different
    # on each contributor's machine) and a project_dir argument that's
    # also absolute. Committing it would force every contributor to
    # share one path layout, which never holds. `cce init` regenerates
    # it on each machine, so gitignoring it is the correct default.
    (".mcp.json", ".mcp.json contains absolute paths regenerated by `cce init`"),
]


def ensure_gitignore(project_dir: str) -> None:
    """Add CCE-related entries to .gitignore if not already present."""
    gitignore = Path(project_dir) / ".gitignore"
    content = gitignore.read_text() if gitignore.exists() else ""

    additions = []
    for entry, comment in _GITIGNORE_ENTRIES:
        if entry not in content:
            additions.append(f"# {comment}\n{entry}")

    if not additions:
        return

    block = "\n\n# CCE (code-context-engine)\n" + "\n".join(additions) + "\n"
    gitignore.write_text(content.rstrip() + block)


def format_for_prompt(commands: dict, label: str = "Project") -> str:
    """Format commands as markdown for the init prompt."""
    if not commands:
        return ""
    lines = []

    # Rules
    rules = commands.get("rules", [])
    if rules and isinstance(rules, list):
        lines.append(f"### {label} Rules")
        for r in rules:
            lines.append(f"- {r}")

    # Preferences
    prefs = commands.get("preferences", {})
    if prefs and isinstance(prefs, dict):
        lines.append(f"### {label} Preferences")
        for k, v in prefs.items():
            lines.append(f"- **{k}:** {v}")

    # Commands
    hook_labels = {
        "before_push": "Before push",
        "before_commit": "Before commit",
        "on_start": "On session start",
    }
    cmd_lines = []
    for hook, hook_label in hook_labels.items():
        cmds = commands.get(hook, [])
        if cmds and isinstance(cmds, list):
            cmd_str = ", ".join(f"`{c}`" for c in cmds)
            cmd_lines.append(f"- **{hook_label}:** {cmd_str}")
    custom = commands.get("custom", {})
    if custom and isinstance(custom, dict):
        cmd_lines.append("- **Custom commands:**")
        for name, cmd in custom.items():
            cmd_lines.append(f"  - `{name}`: `{cmd}`")
    if cmd_lines:
        lines.append(f"### {label} Commands")
        lines.extend(cmd_lines)

    return "\n".join(lines) if lines else ""
