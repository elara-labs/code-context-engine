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

import hashlib
import json
import re
import tomllib
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
}

# ── Instruction file definitions ──────────────────────────────────────

# Editor-agnostic CCE instructions (no "Claude Code" references)
_CCE_INSTRUCTIONS = """\
## Context Engine (CCE)

This project uses Code Context Engine for intelligent code retrieval and
cross-session memory.

### Searching the codebase

**Use `context_search` instead of reading files directly** when exploring
the codebase, answering questions about code, or understanding how things
work. `context_search` returns the most relevant code chunks with
confidence scores instead of whole files.

When to use `context_search`:
- Answering questions about the codebase ("how does X work?", "where is Y?")
- Exploring structure or architecture
- Finding related code, functions, or patterns

Other tools:
- `expand_chunk` for full source of a compressed result
- `related_context` for what calls/imports a function
- `session_recall` to recall past decisions

### Cross-session memory

Call `session_recall("topic phrase")` before answering non-trivial questions.
Call `record_decision(decision="...", reason="...")` after making choices.
Call `record_code_area(file_path="...", description="...")` after meaningful work.
"""

INSTRUCTION_FILES: dict[str, dict] = {
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
    """Stable per-directory slug used as the section name for user-scoped
    editor configs. `<basename>-<6-hex>` so two projects sharing a
    basename ("api", "web", "frontend") get distinct sections instead of
    silently overwriting each other in ~/.codex/config.toml.

    Symlinks are resolved before hashing so two paths pointing at the
    same on-disk directory map to the same slug (idempotent re-runs).
    """
    resolved = project_dir.resolve()
    abs_path = str(resolved)
    h = hashlib.sha256(abs_path.encode()).hexdigest()[:6]
    # TOML bare-key chars here are restricted to ASCII A-Za-z0-9_-; replace
    # anything else (spaces, unicode, punctuation) with `-` so the rendered
    # section is always a syntactically valid bare key. Empty basename (root
    # dir or trailing slash) falls back to "project" so the slug is never
    # just "-a3f2".
    # Use the resolved basename so symlink-vs-real-path produces the same
    # slug — otherwise the hash would match but the basename would differ.
    safe = "".join(
        c if (c.isascii() and (c.isalnum() or c in "-_")) else "-"
        for c in resolved.name
    )
    return f"{safe or 'project'}-{h}"


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
    looked up under each editor's scope root (project dir or home dir)."""
    found = []
    for key, editor in EDITORS.items():
        root = _scope_root(editor, project_dir)
        for marker in editor["detect"]:
            if (root / marker).exists():
                found.append(key)
                break
    return found


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
            data = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    servers = data.setdefault(servers_key, {})
    if "context-engine" in servers:
        existing = servers["context-engine"]
        if existing.get("command") == command and existing.get("args") == entry["args"]:
            return False
        servers["context-engine"] = entry
        atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
        return True

    servers["context-engine"] = entry
    atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
    return True


def _configure_opencode(config_path: Path, command: str, project_dir: str) -> bool:
    """Add CCE to OpenCode's opencode.json. Returns True if changed.

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
            content = config_path.read_text()
            # Strip JSONC comments for parsing
            data = json.loads(_strip_jsonc_comments(content))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    servers = data.setdefault("mcp", {})
    if "context-engine" in servers:
        existing = servers["context-engine"]
        if existing.get("command") == entry["command"] and existing.get("type") == "local":
            return False
        servers["context-engine"] = entry
        atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
        return True

    servers["context-engine"] = entry
    atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
    return True


def _strip_jsonc_comments(text: str) -> str:
    """Strip single-line // comments from JSONC content for JSON parsing."""
    import re
    return re.sub(r'//.*?$', '', text, flags=re.MULTILINE)


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

        original = config_path.read_text()
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
        data = json.loads(config_path.read_text())
        servers = data.get(servers_key, {})
        if "context-engine" not in servers:
            return None
        del servers["context-engine"]
        if servers:
            config_path.write_text(json.dumps(data, indent=2) + "\n")
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
        content = config_path.read_text()
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


def write_instruction_file(project_dir: Path, file_key: str) -> bool:
    """Write CCE instructions to an editor's instruction file. Returns True if written."""
    info = INSTRUCTION_FILES[file_key]
    path = project_dir / info["path"]
    marker = "## Context Engine (CCE)"

    if path.exists():
        content = path.read_text()
        if marker in content:
            return False  # already has CCE block
        # Append
        path.write_text(content.rstrip() + "\n\n" + _CCE_INSTRUCTIONS)
    else:
        path.write_text(_CCE_INSTRUCTIONS)
    return True


def remove_instruction_file(project_dir: Path, file_key: str) -> str | None:
    """Remove CCE block from an editor's instruction file. Returns status or None."""
    info = INSTRUCTION_FILES[file_key]
    path = project_dir / info["path"]
    marker = "## Context Engine (CCE)"

    if not path.exists():
        return None

    content = path.read_text()
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
        path.write_text(new_content + "\n")
        return f"Removed CCE block from {info['name']}"
    else:
        path.unlink()
        return f"Removed {info['name']}"
