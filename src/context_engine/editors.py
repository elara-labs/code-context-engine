"""Multi-editor MCP configuration.

Detects installed editors and writes MCP server config in each editor's
format. Supports Claude Code, VS Code/Copilot, Cursor, Gemini CLI,
OpenAI Codex CLI, OpenCode, and Tabnine.

Two scopes exist for an editor's config:
  - "project" (default): config_path / detect markers resolve under the
    project directory. Each project gets its own config file.
  - "user": config_path / detect markers resolve under the user's home
    directory. One file is shared across all projects, so per-project
    isolation is achieved via a project-derived TOML/JSON section name
    rendered from the editor's `section_template`.

Codex CLI is the only "user" scope today — it reads MCP servers from
~/.codex/config.toml exclusively, not from per-project files.
"""
from __future__ import annotations

import json
import re
import tomllib
from functools import lru_cache
from pathlib import Path

from context_engine.utils import atomic_write_text, resolve_cce_binary


# ── Editor definitions ────────────────────────────────────────────────
# format: "json" (default) or "toml" for Codex
# scope:  "project" (default) or "user" — controls where config_path /
#         detect markers are resolved from. See module docstring.

EDITORS: dict[str, dict] = {
    "claude": {
        "name": "Claude Code",
        "config_path": ".mcp.json",
        "servers_key": "mcpServers",
        "format": "json",
        "detect": [".mcp.json"],
    },
    "vscode": {
        "name": "VS Code / Copilot",
        "config_path": ".vscode/mcp.json",
        "servers_key": "servers",
        "format": "json",
        "detect": [".vscode"],
    },
    "cursor": {
        "name": "Cursor",
        "config_path": ".cursor/mcp.json",
        "servers_key": "mcpServers",
        "format": "json",
        "detect": [".cursor", ".cursorrules"],
    },
    "gemini": {
        "name": "Gemini CLI",
        "config_path": ".gemini/settings.json",
        "servers_key": "mcpServers",
        "format": "json",
        "detect": [".gemini", "GEMINI.md"],
    },
    "codex": {
        "name": "OpenAI Codex",
        "scope": "user",
        # Resolved as ~/.codex/config.toml — Codex CLI reads MCP servers
        # from this user-global file only, never from project-local TOML.
        "config_path": ".codex/config.toml",
        "format": "toml",
        # One section per project. The slug is derived from the project's
        # absolute path so two projects with the same basename can coexist
        # without overwriting each other.
        "section_template": "mcp_servers.cce-{slug}",
        "detect": [".codex"],
    },
    "opencode": {
        "name": "OpenCode",
        "config_path": "opencode.json",
        "servers_key": "mcp",
        "format": "opencode",
        "detect": ["opencode.json", "opencode.jsonc"],
    },
    "tabnine": {
        "name": "Tabnine",
        "config_path": ".tabnine/agent/settings.json",
        "servers_key": "mcpServers",
        "format": "json",
        "detect": [".tabnine"],
    },
    "pi": {
        "name": "Pi",
        "config_path": ".mcp.json",
        "servers_key": "mcpServers",
        "format": "json",
        "detect": [".pi"],
    },
}

# ── Instruction file definitions ──────────────────────────────────────

@lru_cache(maxsize=1)
def get_instructions_base() -> str:
    """Load the agent-neutral instruction template from data/instructions.md."""
    from importlib.resources import files
    return files("context_engine.data").joinpath("instructions.md").read_text(encoding="utf-8")


def _build_instructions(output_level: str = "standard") -> str:
    """Build CCE instructions with the configured output style."""
    from context_engine.compression.output_rules import get_instruction_output_block
    base = get_instructions_base()
    block = get_instruction_output_block(output_level)
    if block:
        return base + "\n" + block + "\n"
    return base


# Default instructions (standard output compression)
_CCE_INSTRUCTIONS = _build_instructions("standard")

INSTRUCTION_FILES: dict[str, dict] = {
    "agents": {
        "name": "AGENTS.md",
        "path": "AGENTS.md",
        "detect": ["AGENTS.md"],
    },
    "copilot": {
        "name": ".github/copilot-instructions.md",
        "path": ".github/copilot-instructions.md",
        "detect": [".github/copilot-instructions.md"],
    },
    "cursorrules": {
        "name": ".cursorrules",
        "path": ".cursorrules",
        "detect": [".cursor", ".cursorrules"],
    },
    "gemini": {
        "name": "GEMINI.md",
        "path": "GEMINI.md",
        "detect": [".gemini", "GEMINI.md"],
    },
    "tabnine": {
        "name": "TABNINE.md",
        "path": "TABNINE.md",
        "detect": [".tabnine", "TABNINE.md"],
    },
}


# ── Scope + slug helpers ──────────────────────────────────────────────

def _scope_root(editor: dict, project_dir: Path) -> Path:
    """Return the directory under which `config_path` and `detect` markers
    are resolved for this editor — project_dir for project-scoped editors
    (the default) or the user's home for user-scoped editors (Codex)."""
    return Path.home() if editor.get("scope") == "user" else project_dir


def _resolved_config_path(editor: dict, project_dir: Path) -> Path:
    return _scope_root(editor, project_dir) / editor["config_path"]


def _project_slug(project_dir: Path) -> str:
    """Stable per-directory slug for user-scoped editor config sections.

    Delegates to utils._project_slug to keep the algorithm in one place.
    """
    from context_engine.utils import _project_slug as _utils_slug
    return _utils_slug(project_dir)


def _editor_section(editor: dict, project_dir: Path) -> str | None:
    """Render the per-project section name from the editor's template, or
    None if the editor uses a single hardcoded section (no per-project
    naming). TOML editors must declare a section_template — they have no
    other way to disambiguate projects sharing one user-global file."""
    tmpl = editor.get("section_template")
    if tmpl is None:
        if editor.get("format") == "toml":
            raise ValueError(
                f"editor {editor.get('name')!r} uses TOML format but has no "
                "section_template; per-project section names are required for "
                "TOML editors so multiple projects don't clash in one file."
            )
        return None
    return tmpl.format(slug=_project_slug(project_dir))


def _toml_quote(s: str) -> str:
    """Escape a string for use inside a double-quoted TOML basic string.

    Without this, paths containing backslashes (Windows: ``C:\\Users\\foo``)
    produce invalid TOML — `\\U` starts a Unicode escape that needs 8 hex
    digits, so a Windows path written verbatim into a `"..."` value parses
    as garbage. Escape order matters: backslashes first, then quotes.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ── Public API ────────────────────────────────────────────────────────

def detect_editors(project_dir: Path) -> list[str]:
    """Return list of editor keys detected for this project. Markers are
    looked up under each editor's scope root (project dir or home dir).

    For Codex, also checks for the VS Code extension directory
    (``~/.vscode/extensions/openai.*``) since the extension doesn't
    create ``~/.codex`` until the CLI is run separately.
    """
    found = []
    for key, editor in EDITORS.items():
        root = _scope_root(editor, project_dir)
        for marker in editor["detect"]:
            if (root / marker).exists():
                found.append(key)
                break
        else:
            # Secondary detection for Codex: VS Code extension installed
            if key == "codex" and _has_vscode_openai_extension():
                found.append(key)
    return found


def _has_vscode_openai_extension() -> bool:
    """Check if any OpenAI VS Code extension is installed (as a proxy for Codex)
    by looking for extension directories matching ``openai.*`` under
    ``~/.vscode/extensions``. No subprocess needed, works cross-platform."""
    ext_dir = Path.home() / ".vscode" / "extensions"
    if not ext_dir.is_dir():
        return False
    return any(ext_dir.glob("openai.*"))


def _codex_toml_block(command: str, project_dir: str, *, section: str) -> str:
    """Generate one TOML mcp_servers block. Section is the full dotted key
    rendered from the editor's section_template (e.g. `mcp_servers.cce-myapp-a3f2`).
    Both `command` and `project_dir` are TOML-escaped — necessary for
    Windows paths with backslashes."""
    cmd = _toml_quote(command)
    proj = _toml_quote(project_dir)
    args_toml = f'"serve", "--project-dir", "{proj}"'
    return f'[{section}]\ncommand = "{cmd}"\nargs = [{args_toml}]\n'


def configure_mcp(project_dir: Path, editor_key: str) -> bool | None:
    """Write MCP config for a specific editor.

    Returns True if changed, False if already configured, or None if the
    config was skipped because the target file could not be read or written.

    Scope-aware: user-scoped editors (Codex) write to a single user-global
    file with a per-project section name; project-scoped editors keep their
    existing per-project file behavior.
    """
    editor = EDITORS[editor_key]
    config_path = _resolved_config_path(editor, project_dir)
    command = resolve_cce_binary()

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Defensive: e.g., ~/.codex exists but is a regular file (antivirus
        # quarantine, manual user weirdness) — that surfaces as
        # FileExistsError on macOS/Linux and NotADirectoryError on Windows.
        # PermissionError can also fire for read-only homes. None of these
        # should bring down the whole `cce init`; treat the editor as not
        # configurable and move on.
        return None

    if editor.get("format") == "toml":
        section = _editor_section(editor, project_dir)
        return _configure_toml(config_path, command, str(project_dir), section=section)

    if editor.get("format") == "opencode":
        return _configure_opencode(config_path, command, str(project_dir))

    servers_key = editor["servers_key"]
    entry = {"command": command, "args": ["serve", "--project-dir", str(project_dir)]}

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Never rewrite a file we couldn't parse: resetting to {} would
            # replace the user's other MCP servers with only our entry.
            # Skip and let the caller surface a warning (same contract as
            # the TOML path).
            return None
        if not isinstance(data, dict):
            return None
    else:
        data = {}

    # A non-dict servers value ("mcpServers": null, or a list) can't hold
    # our entry — replace it with a fresh dict instead of raising TypeError.
    servers = data.get(servers_key)
    if not isinstance(servers, dict):
        servers = {}
        data[servers_key] = servers

    existing = servers.get("context-engine")
    if (
        isinstance(existing, dict)
        and existing.get("command") == command
        and existing.get("args") == entry["args"]
    ):
        return False

    servers["context-engine"] = entry
    atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
    return True


def _configure_opencode(config_path: Path, command: str, project_dir: str) -> bool | None:
    """Add CCE to OpenCode's opencode.json. Returns True if changed, False if
    already configured, or None if the existing config could not be parsed
    (skipped rather than overwritten).

    OpenCode uses a different MCP entry format: type "local" with command
    as an array (not a string + args).
    """
    # OpenCode may also have opencode.jsonc; if the .jsonc exists and .json
    # doesn't, use the .jsonc path instead.
    jsonc_path = config_path.with_suffix(".jsonc")
    if jsonc_path.exists() and not config_path.exists():
        config_path = jsonc_path

    entry = {
        "type": "local",
        "command": [command, "serve", "--project-dir", project_dir],
    }

    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
            # Strip JSONC comments for parsing
            data = json.loads(_strip_jsonc_comments(content))
        except (json.JSONDecodeError, OSError):
            # Never rewrite a file we couldn't parse: resetting to {} would
            # replace the user's other MCP servers with only our entry.
            return None
        if not isinstance(data, dict):
            return None
    else:
        data = {}

    # A non-dict "mcp" value (null, list) can't hold our entry — replace it
    # with a fresh dict instead of raising TypeError.
    servers = data.get("mcp")
    if not isinstance(servers, dict):
        servers = {}
        data["mcp"] = servers

    existing = servers.get("context-engine")
    if (
        isinstance(existing, dict)
        and existing.get("command") == entry["command"]
        and existing.get("type") == "local"
    ):
        return False

    servers["context-engine"] = entry
    atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
    return True


def _strip_jsonc_comments(text: str) -> str:
    """Strip `//` line comments and `/* */` block comments from JSONC content
    for JSON parsing — but only outside strings.

    A naive regex (`//.*?$`) truncates any string value containing `//`
    (e.g. `"url": "https://..."`), which makes json.loads fail and previously
    caused the caller to reset the config to {} and destroy the user's other
    MCP servers. This walker tracks in-string state (including escapes) so
    string contents are preserved verbatim.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                # Escaped char (e.g. \" or \\) — copy it, stay in string.
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            # Line comment: skip to end of line (keep the newline itself).
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            # Block comment: skip to the closing */ (or EOF if unterminated).
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_LEGACY_CODEX_SECTION = "mcp_servers.context-engine"


def _configure_toml(
    config_path: Path,
    command: str,
    project_dir: str,
    *,
    section: str,
) -> bool | None:
    """Add a per-project CCE block to a TOML config file.

    Returns True if changed, False if already configured, or None if the
    config could not be read or written.

    Idempotent: if a block with the same section already exists, returns
    False without rewriting. If the legacy single-block form (the
    pre-multi-project `[mcp_servers.context-engine]`) is present and points
    at this same project, it is replaced in place by the new per-project
    section name — a one-shot migration so anyone who hit the previous
    broken project-local code path doesn't end up with two stale entries.
    """
    block = _codex_toml_block(command, project_dir, section=section)
    marker = f"[{section}]"
    legacy_marker = f"[{_LEGACY_CODEX_SECTION}]"

    try:
        if not config_path.exists():
            atomic_write_text(config_path, block)
            return True

        original = config_path.read_text(encoding="utf-8")
    except OSError:
        return None

    content = original
    dirty = False

    # Legacy migration: drop the old hardcoded `[mcp_servers.context-engine]`
    # block only when it points at this project. Preserve unrelated or
    # user-managed legacy sections rather than guessing ownership.
    if legacy_marker in content and _legacy_codex_section_matches_project(content, project_dir):
        content = _strip_toml_section(content, _LEGACY_CODEX_SECTION)
        dirty = True

    if marker in content:
        # The section already exists, but its values may be stale (the cce
        # binary moved between releases, args drifted, etc.). Parse the TOML
        # and compare; if the existing block doesn't match what we'd write,
        # rewrite it in place rather than reporting "already configured" and
        # leaving Codex pointed at the wrong values.
        if not _toml_section_matches(content, section, command, project_dir):
            content = _strip_toml_section(content, section)
            content = content.rstrip() + "\n\n" + block
            dirty = True
    else:
        content = content.rstrip() + "\n\n" + block
        dirty = True

    if not dirty:
        return False

    try:
        atomic_write_text(config_path, content if content.endswith("\n") else content + "\n")
    except OSError:
        return None
    return True


def _toml_section_matches(
    content: str, section: str, command: str, project_dir: str
) -> bool:
    """Return True iff `[section]` in `content` already specifies the exact
    command + serve args we would write. If a previous install left a stale
    binary path, or the user hand-edited the args, we want to rewrite rather
    than silently report "already configured" and leave Codex pointed at the
    wrong values.

    Section is a dotted path like ``mcp_servers.cce-myapp-a3f2``; we walk
    the parsed dict accordingly."""
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        # Unparseable existing TOML — let the caller rewrite to recover.
        return False

    node: object = parsed
    for part in section.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]

    if not isinstance(node, dict):
        return False
    return (
        node.get("command") == command
        and node.get("args") == ["serve", "--project-dir", project_dir]
    )


def _legacy_codex_section_matches_project(content: str, project_dir: str) -> bool:
    """Return True when the legacy Codex block targets this project."""
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return False

    legacy = parsed.get("mcp_servers", {}).get("context-engine")
    if not isinstance(legacy, dict):
        return False

    args = legacy.get("args")
    return (
        isinstance(args, list)
        and len(args) >= 3
        and args[-2:] == ["--project-dir", project_dir]
    )


def _strip_toml_section(content: str, section: str) -> str:
    """Remove a single `[section]` block (header + body) from TOML text.
    Body ends at the next `[` header at column zero or end of file.

    User content outside the targeted block — header comments, trailing
    comments, blank lines between unrelated sections — is preserved verbatim.
    Only the run of blank lines that surrounded the removed block is
    collapsed back to a single blank line so we don't leave a multi-line
    gap. We deliberately do NOT call `.strip()` on the whole file: that
    would silently delete leading/trailing user content (e.g. a header
    comment at the top of `~/.codex/config.toml`).
    """
    pattern = rf"\[{re.escape(section)}\].*?(?=\n\[|\Z)"
    new_content = re.sub(pattern, "", content, flags=re.DOTALL)
    # Collapse the gap left where the section used to be: 3+ consecutive
    # newlines (i.e. 2+ blank lines) → 2 newlines (1 blank line).
    return re.sub(r"\n{3,}", "\n\n", new_content)


def remove_mcp(project_dir: Path, editor_key: str) -> str | None:
    """Remove CCE from an editor's MCP config. Returns status message or None.

    Symmetrical with `configure_mcp`: only this project's footprint is
    removed. For user-scoped editors (Codex), only the per-project section
    derived from `project_dir` is deleted — other projects' sections in
    the same user-global file are left intact.
    """
    editor = EDITORS[editor_key]
    config_path = _resolved_config_path(editor, project_dir)

    # OpenCode may use .jsonc instead of .json
    if editor.get("format") == "opencode":
        jsonc_path = config_path.with_suffix(".jsonc")
        if jsonc_path.exists() and not config_path.exists():
            config_path = jsonc_path

    if not config_path.exists():
        return None

    if editor.get("format") == "toml":
        section = _editor_section(editor, project_dir)
        # Display path keeps `~` for user-scoped editors so the message
        # reflects what the user actually has on disk (~/.codex/config.toml
        # is more recognisable than /Users/foo/.codex/config.toml).
        if editor.get("scope") == "user":
            display = "~/" + editor["config_path"]
        else:
            display = editor["config_path"]
        return _remove_toml(config_path, display, section=section)

    servers_key = editor["servers_key"]
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        servers = data.get(servers_key, {})
        if "context-engine" not in servers:
            return None
        del servers["context-engine"]
        if servers:
            config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return f"Removed context-engine from {editor['config_path']}"
        else:
            config_path.unlink()
            return f"Removed {editor['config_path']}"
    except (json.JSONDecodeError, OSError):
        return None


def _remove_toml(config_path: Path, display_path: str, *, section: str) -> str | None:
    """Remove a single CCE-managed section from a TOML config file. Returns
    a human-readable status message or None if there was nothing to remove.

    Only the named section is touched; other CCE sections (other projects)
    and unrelated user content are preserved. Section name is regex-escaped
    so it can never accidentally match a longer section that shares a prefix
    (e.g. removing `cce-api` won't touch `cce-api-staging`)."""
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return None

    marker = f"[{section}]"
    if marker not in content:
        return None

    new_content = _strip_toml_section(content, section)
    # Use a whitespace check for "is the file effectively empty?" without
    # mutating new_content — preserving any user comments/whitespace that
    # were in the original file outside the removed section.
    try:
        if new_content.strip():
            if not new_content.endswith("\n"):
                new_content += "\n"
            atomic_write_text(config_path, new_content)
            return f"Removed [{section}] from {display_path}"
        else:
            config_path.unlink()
            return f"Removed {display_path}"
    except OSError:
        return None


def write_instruction_file(
    project_dir: Path, file_key: str, output_level: str = "standard",
) -> bool:
    """Write CCE instructions to an editor's instruction file. Returns True if written."""
    info = INSTRUCTION_FILES[file_key]
    path = project_dir / info["path"]
    marker = "## Context Engine (CCE)"
    path.parent.mkdir(parents=True, exist_ok=True)
    instructions = _build_instructions(output_level)

    if path.exists():
        content = path.read_text(encoding="utf-8")
        if marker in content:
            return False  # already has CCE block
        # Append
        path.write_text(content.rstrip() + "\n\n" + instructions, encoding="utf-8")
    else:
        path.write_text(instructions, encoding="utf-8")
    return True


def remove_instruction_file(project_dir: Path, file_key: str) -> str | None:
    """Remove CCE block from an editor's instruction file. Returns status or None."""
    info = INSTRUCTION_FILES[file_key]
    path = project_dir / info["path"]
    marker = "## Context Engine (CCE)"

    if not path.exists():
        return None

    content = path.read_text(encoding="utf-8")
    if marker not in content:
        return None

    # Remove the CCE block
    start = content.index(marker)
    # Find the next ## heading or end of file
    rest = content[start + len(marker):]
    next_heading = rest.find("\n## ")
    if next_heading >= 0:
        end = start + len(marker) + next_heading
    else:
        end = len(content)

    new_content = (content[:start] + content[end:]).strip()
    if new_content:
        path.write_text(new_content + "\n", encoding="utf-8")
        return f"Removed CCE block from {info['name']}"
    else:
        path.unlink()
        return f"Removed {info['name']}"


# ── Agent Plugin generation ──────────────────────────────────────────


def generate_plugin(
    output_dir: Path,
    version: str,
    output_level: str = "standard",
) -> None:
    """Generate an Agent Plugins v1.0.0 directory.

    Writes plugin.json, mcp.json, and skills/code-context/SKILL.md to
    ``output_dir``. Safe to call repeatedly (overwrites existing files).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # plugin.json
    plugin_manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "code-context-engine",
        "version": version,
        "description": (
            "Index your codebase. AI searches instead of re-reading files. "
            "94% token savings."
        ),
        "author": {
            "name": "Elara Labs",
            "url": "https://github.com/elara-labs/code-context-engine",
        },
        "repository": "https://github.com/elara-labs/code-context-engine",
        "license": "MIT",
        "keywords": [
            "code-search",
            "token-savings",
            "mcp",
            "code-indexing",
            "retrieval",
        ],
    }
    (output_dir / "plugin.json").write_text(
        json.dumps(plugin_manifest, indent=2) + "\n", encoding="utf-8"
    )

    # mcp.json
    mcp_config = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "code-context-engine": {
                "type": "stdio",
                "command": "uvx",
                "args": [
                    "--from",
                    "code-context-engine[local]",
                    "cce",
                    "serve",
                ],
            },
        },
    }
    (output_dir / "mcp.json").write_text(
        json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8"
    )

    # skills/code-context/SKILL.md
    skill_dir = output_dir / "skills" / "code-context"
    skill_dir.mkdir(parents=True, exist_ok=True)

    description = (
        "Intelligent code retrieval and cross-session memory for AI coding "
        "agents. Use when searching codebases, answering questions about "
        "code, exploring architecture, finding functions or patterns, or "
        "recalling past decisions. Provides context_search, expand_chunk, "
        "related_context, session_recall, record_decision, record_code_area, "
        "and set_output_compression MCP tools."
    )
    frontmatter = (
        "---\n"
        "name: code-context\n"
        "description: >\n"
        + "".join(f"  {line}\n" for line in description.splitlines())
        + "license: MIT\n"
        "compatibility: Requires Python 3.11+ and uv (or uvx)\n"
        "metadata:\n"
        "  author: elara-labs\n"
        f'  version: "{version}"\n'
        "---\n\n"
    )
    body = _build_instructions(output_level)
    (skill_dir / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")

    # skills/code-context/references/tools.md
    ref_dir = skill_dir / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)
    from importlib.resources import files
    tools_ref = files("context_engine.data").joinpath("tools_reference.md").read_text(
        encoding="utf-8"
    )
    (ref_dir / "tools.md").write_text(tools_ref, encoding="utf-8")

    # LICENSE (copy from package root if available)
    pkg_license = Path(__file__).parent.parent.parent / "LICENSE"
    if pkg_license.exists():
        (output_dir / "LICENSE").write_text(
            pkg_license.read_text(encoding="utf-8"), encoding="utf-8"
        )
