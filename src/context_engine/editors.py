"""Multi-editor MCP configuration.

Detects installed editors and writes MCP server config in each editor's
format. Supports Claude Code, VS Code/Copilot, Cursor, and Gemini CLI.
"""
from __future__ import annotations

import json
from pathlib import Path

from context_engine.utils import atomic_write_text, resolve_cce_binary


# ── Editor definitions ────────────────────────────────────────────────

EDITORS: dict[str, dict] = {
    "claude": {
        "name": "Claude Code",
        "config_path": ".mcp.json",           # relative to project
        "servers_key": "mcpServers",
        "detect": [".mcp.json"],               # files that indicate this editor
    },
    "vscode": {
        "name": "VS Code / Copilot",
        "config_path": ".vscode/mcp.json",
        "servers_key": "servers",
        "detect": [".vscode"],
    },
    "cursor": {
        "name": "Cursor",
        "config_path": ".cursor/mcp.json",
        "servers_key": "mcpServers",
        "detect": [".cursor", ".cursorrules"],
    },
    "gemini": {
        "name": "Gemini CLI",
        "config_path": ".gemini/settings.json",
        "servers_key": "mcpServers",
        "detect": [".gemini", "GEMINI.md"],
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
}


# ── Public API ────────────────────────────────────────────────────────

def detect_editors(project_dir: Path) -> list[str]:
    """Return list of editor keys detected in the project directory."""
    found = []
    for key, editor in EDITORS.items():
        for marker in editor["detect"]:
            if (project_dir / marker).exists():
                found.append(key)
                break
    return found


def configure_mcp(project_dir: Path, editor_key: str) -> bool:
    """Write MCP config for a specific editor. Returns True if changed."""
    editor = EDITORS[editor_key]
    config_path = project_dir / editor["config_path"]
    servers_key = editor["servers_key"]
    command = resolve_cce_binary()
    entry = {"command": command, "args": ["serve", "--project-dir", str(project_dir)]}

    config_path.parent.mkdir(parents=True, exist_ok=True)

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


def remove_mcp(project_dir: Path, editor_key: str) -> str | None:
    """Remove CCE from an editor's MCP config. Returns status message or None."""
    editor = EDITORS[editor_key]
    config_path = project_dir / editor["config_path"]
    servers_key = editor["servers_key"]

    if not config_path.exists():
        return None

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
