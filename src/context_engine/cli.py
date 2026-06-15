# src/context_engine/cli.py
"""CLI entry point for code-context-engine."""
import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import click

# Windows consoles default to cp1252 which can't encode the Unicode box-drawing
# and symbol characters used in CCE's output. Reconfigure to UTF-8 early so
# output doesn't crash on first run. errors='replace' is a safety net in case
# the terminal still can't render a character.
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from context_engine.config import load_config, resolve_ollama_url, PROJECT_CONFIG_NAME
from context_engine.utils import project_storage_dir


def _safe_cwd() -> Path:
    """Return `Path.cwd()` or raise a `click.ClickException` with a
    friendly, actionable error if the OS denies access.

    On macOS, `os.getcwd()` can fail with `PermissionError` /
    `FileNotFoundError` / `OSError` when:
      · The terminal lacks Full Disk Access (newer macOS sandboxing).
      · The current directory was deleted or renamed while the shell
        was sitting in it (the shell remembers the inode but the OS
        won't let the process read it).
      · iCloud Drive / Dropbox is mid-sync on the path.
      · The directory lives behind a fuse / virtualised mount whose
        permissions changed under us.

    Without this wrapper every cce subcommand crashes with a 30-line
    pathlib stack trace that gives the user nothing to act on. With it,
    Click's exception machinery prints `Error: <message>` and exits 1
    cleanly. Used at every Path.cwd() callsite in this module.
    """
    try:
        return Path.cwd()
    except (PermissionError, FileNotFoundError, OSError) as exc:
        raise click.ClickException(
            f"Cannot read the current working directory "
            f"({exc.__class__.__name__}: {exc}).\n\n"
            "On macOS this usually means your terminal lacks Full Disk Access:\n"
            "  System Settings → Privacy & Security → Full Disk Access\n"
            "  → enable for your terminal app, then restart it.\n\n"
            "Otherwise the directory may have been deleted/renamed while "
            "the shell was open — `cd` into a directory you can read "
            "and try again."
        ) from exc


# ── Update check ─────────────────────────────────────────────────────
_CCE_HOME = Path.home() / ".cce"
_UPDATE_CACHE = _CCE_HOME / "update_check.json"
_UPDATE_CHECK_TTL = 24 * 3600  # 1 day


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse '0.4.21' into (0, 4, 21) for comparison."""
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def _check_for_update() -> str | None:
    """Return the latest PyPI version if newer than installed, else None.

    Checks at most once per day. Best-effort: swallows all errors.
    """
    import time
    from importlib.metadata import version as pkg_version

    try:
        current = pkg_version("code-context-engine")
    except Exception:
        return None

    # Read cache
    try:
        if _UPDATE_CACHE.exists():
            data = json.loads(_UPDATE_CACHE.read_text())
            if time.time() - data.get("ts", 0) < _UPDATE_CHECK_TTL:
                latest = data.get("latest", "")
                if latest and _version_tuple(latest) > _version_tuple(current):
                    return latest
                return None
    except Exception:
        pass

    # Fetch from PyPI
    latest = None
    try:
        import httpx
        resp = httpx.get(
            "https://pypi.org/pypi/code-context-engine/json",
            timeout=3.0,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            latest = resp.json()["info"]["version"]
    except Exception:
        pass

    # Cache result
    try:
        _CCE_HOME.mkdir(parents=True, exist_ok=True)
        _UPDATE_CACHE.write_text(json.dumps({"ts": time.time(), "latest": latest or ""}))
    except Exception:
        pass

    if latest and _version_tuple(latest) > _version_tuple(current):
        return latest
    return None


def _show_update_notice() -> None:
    """Print a one-line update notice if a newer version is available."""
    from importlib.metadata import version as pkg_version

    try:
        latest = _check_for_update()
        if latest:
            current = pkg_version("code-context-engine")
            click.echo(
                f"\n  {click.style('Update available', fg='yellow', bold=True)} "
                f"{click.style(current, dim=True)} → "
                f"{click.style(latest, fg='green', bold=True)}  "
                f"{click.style('Run', dim=True)} "
                f"{click.style('cce upgrade', fg='cyan')} "
                f"{click.style('to update', dim=True)}"
            )
    except Exception:
        pass


def _configure_mcp(project_dir: Path) -> bool:
    """Write MCP server config to .mcp.json in the project directory.

    Returns True if the entry was added. Uses an atomic write so a crash or
    partial write can't destroy pre-existing MCP server entries in the file.
    """
    from context_engine.utils import atomic_write_text, resolve_cce_binary

    mcp_path = project_dir / ".mcp.json"
    # `sys.executable` is wrong for non-venv installs (pipx, Homebrew, pip
    # --user) — its parent has the python interpreter, not necessarily `cce`.
    # `resolve_cce_binary` matches the same fallbacks the SessionStart hook
    # uses so MCP and the hook agree on which `cce` to call.
    command = resolve_cce_binary()

    entry = {"command": command, "args": ["serve", "--project-dir", str(project_dir)]}

    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    servers = data.setdefault("mcpServers", {})
    if "context-engine" in servers:
        existing = servers["context-engine"]
        if existing.get("command") == command and existing.get("args") == entry["args"]:
            return False  # already configured and up to date
        # Update stale command path or args (e.g. after package rename).
        servers["context-engine"] = entry
        atomic_write_text(mcp_path, json.dumps(data, indent=2) + "\n")
        return True

    servers["context-engine"] = entry
    atomic_write_text(mcp_path, json.dumps(data, indent=2) + "\n")
    return True


_CCE_CLAUDE_MD_MARKER = "## Context Engine (CCE)"
# Version stamp embedded as an HTML comment so it doesn't render in the final
# Markdown but lets `_ensure_claude_md` detect when the installed block is
# stale and needs replacing. Bump whenever _CCE_CLAUDE_MD_BLOCK changes.
_CCE_CLAUDE_MD_VERSION = "4"
_CCE_CLAUDE_MD_VERSION_TAG = f"<!-- cce-block-version: {_CCE_CLAUDE_MD_VERSION} -->"
_CCE_CLAUDE_MD_VERSION_PREFIX = "<!-- cce-block-version: "
_CCE_CLAUDE_MD_END_MARKER = "<!-- /cce-block -->"

_CCE_CLAUDE_MD_BLOCK_TEMPLATE = f"""\
{_CCE_CLAUDE_MD_VERSION_TAG}
## Context Engine (CCE)

This project uses Code Context Engine for intelligent code retrieval and
cross-session memory.

### Searching the codebase

**You MUST use `context_search` instead of reading files directly** when
exploring the codebase, answering questions about code, or understanding how
things work. This is a hard requirement, not a suggestion. `context_search`
returns the most relevant code chunks with confidence scores instead of whole
files, and tracks token savings automatically.

When to use `context_search`:
- Answering questions about the codebase ("how does X work?", "where is Y?")
- Exploring structure or architecture
- Finding related code, functions, or patterns
- Any time you would otherwise read a file just to understand it

When to use `Read` instead:
- You need to edit a specific file (read before editing)
- You need the exact, complete content of a known file path

Other search tools:
- `expand_chunk` — get full source for a compressed result
- `related_context` — find what calls/imports a function

### Cross-session memory — use it actively

This project has persistent memory across Claude Code sessions. **You must
use it both ways: recall before answering, record after deciding.** Memory
that is not recorded is lost; memory that is not recalled does nothing.

**Before answering a non-trivial question, call `session_recall`.**
Especially when:
- The question touches architecture, design, or naming choices
- The user asks "what / why / how did we ..."
- You are about to recommend an approach the team may have already chosen
  or already rejected

Pass a topic phrase, not a single word — e.g. `session_recall("auth flow")`,
not `session_recall("auth")`. Recall is vector-similarity-based, so paraphrases
match. If recall returns relevant entries, lead with them ("Per a prior
decision: ...") instead of re-deriving the answer.

**After making a non-obvious decision, call `record_decision`.** Especially:
- Choosing one library / pattern / approach over another
- Resolving an ambiguity in the spec or requirements
- Establishing a convention the project should follow going forward
- Anything you would not want to re-litigate next session

Format: `record_decision(decision="...", reason="...")`. Keep both fields
short and specific — they are surfaced verbatim at the start of future
sessions.

**After meaningful work in a file, call `record_code_area`.** Especially when:
- You added or substantially modified a function/class
- You traced through a non-obvious flow and want future-you to find it fast

Format: `record_code_area(file_path="...", description="...")`.

Skip recording for trivial reads, formatting changes, or one-off lookups —
the goal is durable signal, not an event log.

### Drilling deeper from a recall hit

`session_recall` results are tagged with the source session id, e.g.
`[turn sid:abc123|n:5]`. To drill in:

- `session_timeline(session_id="abc123")` — walk the per-turn summaries of
  that session in order. Use this when the user asks "what was the
  reasoning?" or "how did we get there?".
- `session_event(event_id=N)` — fetch a specific tool event's raw input
  and output (capped at 4 KB at read time). Use this when a turn summary
  references a tool result you actually need to inspect.

Both are read-only and cheap. Prefer them over re-running tool calls or
asking the user to re-paste context.

{{output_style}}
{_CCE_CLAUDE_MD_END_MARKER}
"""


def _build_claude_md_block(output_level: str = "standard") -> str:
    """Generate the CLAUDE.md CCE block with the configured output style."""
    from context_engine.compression.output_rules import get_instruction_output_block
    block = get_instruction_output_block(output_level)
    return _CCE_CLAUDE_MD_BLOCK_TEMPLATE.replace("{output_style}", block)


# Default block for backward compat
_CCE_CLAUDE_MD_BLOCK = _build_claude_md_block("standard")


def _resolve_cce_cmd() -> str:
    """Find the globally installed cce binary path."""
    from context_engine.utils import resolve_cce_binary
    return resolve_cce_binary()


def _has_cce_hook(hook_list: list, marker: str) -> bool:
    """Check if a CCE hook already exists in a hooks list."""
    for entry in hook_list:
        for h in entry.get("hooks", []):
            if marker in h.get("command", ""):
                return True
    return False


def _install_memory_hooks(project_dir: Path) -> None:
    """Install the 5 lifecycle hooks for memory capture (PR 2).

    Writes ~/.cce/hooks/cce_hook.sh and wires <project>/.claude/settings.json
    entries for SessionStart, UserPromptSubmit, PostToolUse, Stop, SessionEnd.
    Idempotent.
    """
    from context_engine.memory.hook_installer import (
        install_hook_script, install_settings,
    )
    install_hook_script()
    summary = install_settings(project_dir)
    if summary["added"]:
        _ok(
            "Memory hooks installed  "
            + _dim(f"({len(summary['added'])} hooks: {', '.join(summary['added'])})")
        )
    elif summary["skipped"]:
        _ok("Memory hooks already configured")


def _check_memory_capture_reachable(config, project_dir: Path) -> None:
    """Probe the loopback hook server so the user knows whether `cce serve` is
    actually running before they restart Claude Code expecting capture to work.

    Hooks fail closed (`curl ... || true`), so a missing daemon means capture
    is *silently* disabled — exactly the onboarding footgun this guards
    against. We never block init; we just print clear next steps.
    """
    import socket
    project_name = project_dir.name
    storage_base = project_storage_dir(config, project_dir)
    # Try the storage-local file first (authoritative), then fall back to
    # the default-path rendezvous file `cce serve` writes for the hook
    # shell script. Either is sufficient for the probe.
    candidates = [
        storage_base / "serve.port",
        Path.home() / ".cce" / "projects" / project_name / "serve.port",
    ]
    port_file = next((p for p in candidates if p.exists()), None)
    if port_file is None:
        _warn(
            "Memory capture not yet active — `cce serve` hasn't been started "
            "for this project."
        )
        click.echo(
            _dim(
                "    Run `cce serve` in a separate terminal so the loopback "
                "hook server starts;\n"
                "    until it's running, hooks fire successfully but capture "
                "is silently dropped.\n"
                "    Verify any time with `cce sessions status`."
            )
        )
        return
    try:
        port = int(port_file.read_text().strip())
    except (OSError, ValueError):
        _warn(f"Memory capture port file unreadable at {port_file}")
        return
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass
    except OSError:
        _warn(
            f"Memory capture stale — found serve.port at :{port} but "
            "nothing is listening."
        )
        click.echo(
            _dim(
                "    Either `cce serve` exited or is bound to a different "
                "port now.\n"
                "    Restart it; the new port replaces the stale file on "
                "first hook fire."
            )
        )
        return
    _ok("Memory capture active  " + _dim(f"(127.0.0.1:{port} reachable)"))


def _ensure_session_hook(project_dir: Path) -> None:
    """Add Claude Code hooks so CCE status shows on startup."""
    settings_dir = project_dir / ".claude"
    settings_dir.mkdir(exist_ok=True)
    settings_path = settings_dir / "settings.local.json"

    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    hooks = data.setdefault("hooks", {})
    cce_cmd = _resolve_cce_cmd()
    changed = False

    # SessionStart hook — show CCE status
    session_hooks = hooks.setdefault("SessionStart", [])
    if not _has_cce_hook(session_hooks, "cce status"):
        session_hooks.append({
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{cce_cmd} status --oneline"}],
        })
        changed = True

    if changed:
        settings_path.write_text(json.dumps(data, indent=2) + "\n")
        _ok("SessionStart hook installed for CCE status")


from context_engine.cli_style import success, warn as _warn_style, dim as _dim_style, value, header, label, CHECK, CROSS, DOT, ARROW


def _ok(msg: str) -> None:
    """Print a green ✓ success line."""
    click.echo(f"  {CHECK} {msg}")


def _warn(msg: str) -> None:
    """Print a yellow ! warning line."""
    click.echo(f"  {DOT} {_warn_style(msg)}")


def _dim(msg: str) -> str:
    return _dim_style(msg)


def _show_welcome_banner(config) -> None:
    """Show an animated welcome banner when cce is run with no subcommand."""
    import json as _json
    import random
    import re
    import time
    from importlib.metadata import version as pkg_version

    try:
        ver = pkg_version("code-context-engine")
    except Exception:
        ver = "?"

    project_dir = _safe_cwd()
    project_name = project_dir.name
    storage_dir = project_storage_dir(config, project_dir)

    # Gather stats
    chunks = 0
    queries = 0
    full_file = 0
    served = 0
    saved_pct = 0
    try:
        from context_engine.storage.vector_store import VectorStore
        vs = VectorStore(db_path=str(storage_dir / "vectors"))
        chunks = vs.count()
    except Exception:
        pass
    stats_path = storage_dir / "stats.json"
    if stats_path.exists():
        try:
            stats = _json.loads(stats_path.read_text())
            queries = stats.get("queries", 0)
            full_file = stats.get("full_file_tokens", 0)
            served = stats.get("served_tokens", 0)
            if full_file > 0:
                saved_pct = int((full_file - served) / full_file * 100)
        except Exception:
            pass

    # Ollama check
    ollama_running = False
    ollama_model = getattr(config, "compression_model", "phi3:mini")
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=1.0)
        if resp.status_code == 200:
            ollama_running = True
    except Exception:
        pass

    embedding_model = getattr(config, "embedding_model", "BAAI/bge-small-en-v1.5")
    compression_mode = f"LLM ({ollama_model})" if ollama_running else "truncation"
    profile = config.detect_resource_profile()
    indexed = chunks > 0

    icons = ["⛁", "◈", "⬡", "◉", "⏣", "⎔", "▣", "◇", "⬢", "❖"]
    icon = random.choice(icons)

    # ── ANSI helpers ──
    _ANSI_RE = re.compile(r"\033\[[0-9;]*m")

    def _vis_len(s: str) -> int:
        """Visible length of a string (strips ANSI codes)."""
        return len(_ANSI_RE.sub("", s))

    # Color shortcuts that return styled text
    C = "\033[36m"       # cyan
    CB = "\033[1;36m"    # cyan bold
    G = "\033[32m"       # green
    GB = "\033[1;32m"    # green bold
    Y = "\033[33m"       # yellow
    WB = "\033[1;37m"    # white bold
    D = "\033[2m"        # dim
    M = "\033[35m"       # magenta
    R = "\033[0m"        # reset

    # ── Layout constants ──
    # Box: │ <LW> │ <RW> │  with 1 space padding each side
    # Total = 1 + 1 + LW + 1 + 1 + 1 + RW + 1 + 1 = LW + RW + 7
    LW = 42
    RW = 38
    W = LW + RW + 7
    FW = W - 2

    def _rpad(text: str, width: int) -> str:
        """Right-pad styled text to exact visible width."""
        vl = _vis_len(text)
        return text + " " * max(0, width - vl)

    def _center(text: str, width: int) -> str:
        """Center styled text in exact visible width."""
        vl = _vis_len(text)
        lp = max(0, (width - vl) // 2)
        rp = max(0, width - vl - lp)
        return " " * lp + text + " " * rp

    def full_line(text: str) -> str:
        return f"{D}│{R} {_center(text, FW - 2)} {D}│{R}"

    def empty_line() -> str:
        return f"{D}│{R}{' ' * FW}{D}│{R}"

    def two_col(left: str, right: str) -> str:
        l = _rpad(left, LW)
        r = _rpad(right, RW)
        return f"{D}│{R} {l} {D}│{R} {r} {D}│{R}"

    # ── Borders ──
    title = f" Code Context Engine v{ver} "
    dashes = W - 2 - len(title)
    ld = dashes // 2
    rd = dashes - ld

    top_border = f"{D}╭{'─' * ld}{R}{CB}{title}{R}{D}{'─' * rd}╮{R}"
    mid_border = f"{D}├{'─' * (LW + 2)}┬{'─' * (RW + 2)}┤{R}"
    bot_border = f"{D}╰{'─' * (LW + 2)}┴{'─' * (RW + 2)}╯{R}"

    # ── Build output ──
    out: list[str] = []

    # Header (full width)
    out.append(top_border)
    out.append(empty_line())
    out.append(full_line(f"{CB}{icon}  C C E  {icon}{R}"))
    out.append(empty_line())
    out.append(full_line(f"{WB}{project_name}{R}"))
    out.append(full_line(f"{D}{profile} profile  ·  {project_dir}{R}"))
    out.append(empty_line())

    # Two-column section
    out.append(mid_border)

    # Build left lines
    left_lines: list[str] = []
    left_lines.append(f"{WB}Status{R}")
    if indexed:
        left_lines.append(f" {G}●{R} Indexed      {C}{chunks:,} chunks{R}")
        left_lines.append(f" {G}●{R} Embedding    {C}{embedding_model}{R}")
        if ollama_running:
            left_lines.append(f" {G}●{R} Ollama       {G}running{R}")
        else:
            left_lines.append(f" {Y}○{R} Ollama       {Y}not running{R}")
        left_lines.append(f" {G}●{R} Compress     {C}{compression_mode}{R}")
        if queries > 0:
            left_lines.append(f" {G}●{R} Savings      {GB}{saved_pct}%{R} over {C}{queries}{R} queries")
        elif full_file > 0:
            left_lines.append(f" {D}○ Savings      waiting for first search{R}")
            left_lines.append(f"   {D}stats update after context_search calls{R}")
    else:
        left_lines.append(f" {Y}○ Not indexed{R}")
        left_lines.append(f"   {D}run: cce init{R}")

    # Build right lines
    right_lines: list[str] = []
    right_lines.append(f"{WB}Getting started{R}")
    if not indexed:
        right_lines.append(f" {C}cce init{R}      {D}setup project{R}")
    right_lines.append(f" {C}cce status{R}    {D}full diagnostics{R}")
    right_lines.append(f" {C}cce savings{R}   {D}token savings{R}")
    right_lines.append(f" {C}cce list{R}      {D}all commands{R}")
    right_lines.append("")
    right_lines.append(f"{D}{'─' * RW}{R}")
    right_lines.append(f" {D}Embed:{R}  {M}{embedding_model}{R}")
    if ollama_running:
        right_lines.append(f" {D}Ollama:{R} {G}running ({ollama_model}){R}")
    else:
        right_lines.append(f" {D}Ollama:{R} {Y}not running{R}")

    # Pad to same height
    max_h = max(len(left_lines), len(right_lines))
    while len(left_lines) < max_h:
        left_lines.append("")
    while len(right_lines) < max_h:
        right_lines.append("")

    for lt, rt in zip(left_lines, right_lines):
        out.append(two_col(lt, rt))

    out.append(bot_border)

    # ── Animate ──
    click.echo()
    is_tty = sys.stdout.isatty()
    for i, line in enumerate(out):
        click.echo(line)
        if is_tty and i < 8:
            time.sleep(0.03)
    click.echo()


def _preflight_check(config) -> None:
    """Verify all required components are ready before indexing starts.

    Auto-detects an embedding backend (fastembed → Ollama), reports which
    one was picked, and surfaces Ollama status for the separate compression
    path so users know what compression level they will get.
    """
    # --- SQLite extension support ---
    import sqlite3 as _sqlite3
    _test_conn = _sqlite3.connect(":memory:")
    if not hasattr(_test_conn, "enable_load_extension"):
        _test_conn.close()
        raise click.ClickException(
            "Your Python was compiled without SQLite extension support "
            "(enable_load_extension is missing).\n"
            "This is common with python.org installers on macOS.\n\n"
            "Fix: reinstall CCE under a Python that has extension support:\n\n"
            "  brew install python3\n"
            "  uv tool install --python /opt/homebrew/bin/python3 "
            "--force code-context-engine\n"
        )
    _test_conn.close()

    # --- Embedding backend ---
    click.echo(_dim("  Detecting embedding backend") + "...", nl=False)
    from context_engine.config import resolve_ollama_url
    ollama_model = getattr(config, "ollama_embed_model", "nomic-embed-text")
    ollama_url = resolve_ollama_url(config)
    try:
        from context_engine.indexer.embedder import select_backend
        # Don't echo a tentative "loading fastembed…" or "using Ollama…"
        # banner before select_backend() picks. CCE_EMBED_BACKEND can
        # force a different choice than the probe order suggests, and
        # printing both messages produced contradictory output. Wait for
        # the actual selection, then echo once with the truth.
        backend = select_backend(
            model_name=getattr(config, "embedding_model", "BAAI/bge-small-en-v1.5"),
            ollama_model=ollama_model,
            ollama_url=ollama_url,
        )
        click.echo(
            " " + click.style(
                f"ready ({backend.name}, {backend.dimension}-d, {backend.model_name})",
                fg="green",
            )
        )
    except Exception:
        click.echo("")
        raise click.ClickException(
            "No embedding backend available.\n\n"
            "Fix (pick one):\n"
            "  1. Install local embeddings:\n"
            "     uv tool install 'code-context-engine[local]'\n\n"
            f"  2. Start Ollama and pull the embedding model:\n"
            f"     ollama pull {ollama_model}\n"
        )

    # --- Ollama for LLM compression (independent of the embedding path) ---
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        if resp.status_code == 200:
            click.echo(
                "  Ollama " + click.style("detected", fg="green") +
                " — LLM summarization enabled."
            )
        else:
            click.echo(
                "  Ollama " + click.style("not running", fg="yellow") +
                " — using truncation compression."
            )
            click.echo(_dim("  Tip: ollama pull phi3:mini for LLM summarization"))
    except Exception:
        click.echo(
            "  Ollama " + click.style("not running", fg="yellow") +
            " — using truncation compression."
        )
        click.echo(_dim("  Tip: ollama pull phi3:mini for LLM summarization"))


def _ensure_claude_md(project_dir: Path, output_level: str = "standard") -> None:
    """Add or upgrade the CCE instructions block in CLAUDE.md.

    Three states the file can be in:
      - Missing: write the block.
      - Has the current version (matching version tag): no-op.
      - Has an older version OR a pre-versioned block: replace just the CCE
        block, preserving everything else the user wrote in CLAUDE.md.

    Without the upgrade path, projects installed with v0.2.x kept the old
    instructions forever — Claude never learned to call record_decision /
    session_recall and the cross-session memory loop stayed broken.
    """
    from context_engine.utils import atomic_write_text

    block = _build_claude_md_block(output_level)
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        atomic_write_text(claude_md, block)
        _ok("CLAUDE.md created with CCE instructions")
        return

    existing = claude_md.read_text()

    # Already on the current version — nothing to do.
    if _CCE_CLAUDE_MD_VERSION_TAG in existing:
        return

    # An older versioned block OR a pre-versioned block (just the marker).
    # Replace it in place so any custom content the user added around it
    # survives the upgrade.
    old_block = _extract_existing_cce_block(existing)
    if old_block is not None:
        new_content = existing.replace(old_block, block.rstrip(), 1)
        atomic_write_text(claude_md, new_content)
        _ok("CLAUDE.md upgraded to current CCE instructions")
        return

    # No CCE block detected — append.
    new_content = existing.rstrip() + "\n\n" + block
    atomic_write_text(claude_md, new_content)
    _ok("CLAUDE.md updated with CCE instructions")


def _extract_existing_cce_block(content: str) -> str | None:
    """Return the existing CCE block text from a CLAUDE.md, or None.

    Recognises both the new versioned form (version tag → end marker) and
    the legacy unmarked form (the `## Context Engine (CCE)` heading through
    end-of-file). Returns the slice without trailing whitespace so the
    caller can do an exact string-replace.
    """
    # New format: bounded by version tag and end marker.
    if _CCE_CLAUDE_MD_VERSION_PREFIX in content and _CCE_CLAUDE_MD_END_MARKER in content:
        start = content.find(_CCE_CLAUDE_MD_VERSION_PREFIX)
        end_pos = content.find(_CCE_CLAUDE_MD_END_MARKER, start)
        if start != -1 and end_pos != -1:
            end_pos += len(_CCE_CLAUDE_MD_END_MARKER)
            return content[start:end_pos].rstrip()

    # Legacy format: the marker heading through the next top-level heading
    # or end of file. Conservative — if the user put their own H2 right
    # after the CCE block, we stop there and don't eat user content.
    if _CCE_CLAUDE_MD_MARKER not in content:
        return None
    start = content.find(_CCE_CLAUDE_MD_MARKER)
    after_start = content.find("\n## ", start + len(_CCE_CLAUDE_MD_MARKER))
    end = after_start if after_start != -1 else len(content)
    return content[start:end].rstrip()


@click.group(invoke_without_command=True)
@click.version_option(package_name="code-context-engine")
@click.option("--verbose", "-v", is_flag=True, help="Enable detailed logging output")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """code-context-engine — Local context engine for AI coding assistants."""
    ctx.ensure_object(dict)
    project_path = _safe_cwd() / PROJECT_CONFIG_NAME
    ctx.obj["config"] = load_config(project_path=project_path if project_path.exists() else None)
    ctx.obj["verbose"] = verbose

    if ctx.invoked_subcommand is None:
        _show_welcome_banner(ctx.obj["config"])


@main.result_callback()
@click.pass_context
def _after_command(ctx: click.Context, *_args, **_kwargs) -> None:
    """Run after every command. Shows update notice if available."""
    # Skip for serve (long-running), upgrade (already handles it),
    # and any --json output (would corrupt parseable output).
    if ctx.invoked_subcommand in ("serve", "upgrade"):
        return
    if "--json" in sys.argv:
        return
    _show_update_notice()


_INIT_AGENT_CHOICES = ("auto", "claude", "codex", "copilot", "all")
_INIT_AGENT_TO_EDITORS = {
    "claude": {"claude"},
    "codex": {"codex"},
    "copilot": {"vscode"},
}
# Editor key → instruction-file key. `claude` is omitted because CLAUDE.md is
# written by `_ensure_claude_md`, not via the generic instruction-file path.
# `opencode` has no instruction file.
_INIT_EDITOR_TO_INSTRUCTIONS = {
    "codex": "agents",
    "vscode": "copilot",
    "cursor": "cursorrules",
    "gemini": "gemini",
    "tabnine": "tabnine",
}


def _init_editor_targets(project_dir: Path, agent: str) -> set[str]:
    """Return editor keys to configure for `cce init --agent`.

    - `all`: every editor in EDITORS (computed at call time so the set never
      drifts when new editors are added).
    - `auto`: Claude plus any editor whose project/home markers exist.
    - explicit (`claude`/`codex`/`copilot`): exactly the editors that flag
      maps to.
    """
    from context_engine.editors import EDITORS, detect_editors

    if agent == "all":
        return set(EDITORS.keys())
    if agent != "auto":
        return set(_INIT_AGENT_TO_EDITORS[agent])
    return {"claude", *detect_editors(project_dir)}


def _init_instruction_targets(editor_targets: set[str]) -> set[str]:
    """Instruction-file keys derived from the selected editors."""
    return {
        file_key
        for editor_key, file_key in _INIT_EDITOR_TO_INSTRUCTIONS.items()
        if editor_key in editor_targets
    }


@main.command()
@click.option(
    "--agent",
    type=click.Choice(_INIT_AGENT_CHOICES),
    default="auto",
    show_default=True,
    help="Agent/editor target: auto, claude, codex, copilot, or all.",
)
@click.pass_context
def init(ctx: click.Context, agent: str) -> None:
    """Initialize context engine and connect it to AI coding agents."""
    from context_engine.indexer.git_hooks import install_hooks
    from context_engine.project_commands import ensure_gitignore
    config = ctx.obj["config"]
    project_dir = _safe_cwd()

    click.echo("")
    click.echo(
        click.style("  Code Context Engine", fg="cyan", bold=True) +
        click.style(f"  ·  {project_dir.name}", fg="white", bold=True)
    )
    click.echo(_dim("  " + "─" * 44))
    click.echo("")

    # 1. Pre-flight: verify embedding model + report Ollama status
    _preflight_check(config)
    click.echo("")

    # 2. Storage
    storage_dir = project_storage_dir(config, project_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    meta_path = storage_dir / "meta.json"
    meta_path.write_text(json.dumps({"project_dir": str(project_dir.resolve())}))

    # 3. Git hooks
    is_git_repo = (project_dir / ".git").exists()
    if is_git_repo:
        installed = install_hooks(str(project_dir))
        if installed:
            _ok("Git hooks installed  " + _dim(f"({len(installed)} hooks, auto-updates on commit)"))
    else:
        _warn("Not a git repository — git hook skipped")
        click.echo(_dim("    Run `cce index` manually after making changes."))

    # 4. MCP config — selected agents/editors
    from context_engine.editors import (
        EDITORS, INSTRUCTION_FILES,
        configure_mcp, write_instruction_file,
    )
    from context_engine.editors import _editor_section  # noqa: SLF001

    editor_targets = _init_editor_targets(project_dir, agent)
    if "claude" in editor_targets:
        configured = _configure_mcp(project_dir)
        if configured:
            _ok("MCP server registered in " + click.style(".mcp.json", fg="cyan"))
        else:
            _ok("MCP server already configured in " + click.style(".mcp.json", fg="cyan"))

    for editor_key in sorted(editor_targets):
        if editor_key == "claude":
            continue
        editor = EDITORS[editor_key]
        changed = configure_mcp(project_dir, editor_key)
        if changed is None:
            _warn(f"MCP server skipped for {editor['name']} (could not read or write config file)")
            continue
        verb = "registered" if changed else "already configured"
        _ok(f"MCP server {verb} for {editor['name']}")
        # User-scoped editors (Codex) share one config file across all
        # projects, so surface the file + per-project section so users
        # can see what landed where (and which block to remove by hand
        # if they ever skip `cce uninstall`).
        if editor.get("scope") == "user":
            section = _editor_section(editor, project_dir)
            click.echo(_dim(f"    ~/{editor['config_path']}  →  [{section}]"))

    # Write instruction files for the selected editors. In `auto` mode, also
    # pick up instruction files whose marker exists even if the editor itself
    # wasn't detected (e.g. an `AGENTS.md` checked in without a `~/.codex/`).
    # Explicit `--agent X` writes only what X covers — no surprise edits.
    instruction_targets = _init_instruction_targets(editor_targets)
    if agent == "auto":
        for file_key, info in INSTRUCTION_FILES.items():
            if any((project_dir / marker).exists() for marker in info["detect"]):
                instruction_targets.add(file_key)
    output_level = getattr(config, "output_compression", "standard")
    for file_key in sorted(instruction_targets):
        info = INSTRUCTION_FILES[file_key]
        if write_instruction_file(project_dir, file_key, output_level=output_level):
            _ok(f"CCE instructions added to {info['name']}")

    # 5. CLAUDE.md + session hook + memory lifecycle hooks
    if "claude" in editor_targets:
        _ensure_claude_md(project_dir, output_level=output_level)
        _ensure_session_hook(project_dir)
        _install_memory_hooks(project_dir)
        _check_memory_capture_reachable(config, project_dir)

    # 6. .gitignore — add CCE per-machine entries
    ensure_gitignore(str(project_dir))
    _ok(".gitignore updated with CCE entries")

    click.echo("")
    click.echo(
        "  " + click.style("Indexing project", fg="cyan", bold=True) + "..."
    )
    asyncio.run(_run_index(config, str(project_dir), full=True))
    click.echo("")
    click.echo(
        click.style("  Done!", fg="green", bold=True) +
        click.style("  Restart your AI coding agent to activate CCE.", fg="white")
    )
    click.echo("")


@main.command()
@click.option("--full", is_flag=True, help="Force full re-index of every file")
@click.option("--path", type=str, default=None, help="Index only this file or directory")
@click.pass_context
def index(ctx: click.Context, full: bool, path: str | None) -> None:
    """Index or re-index project files."""
    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]
    project_dir = str(_safe_cwd())
    from context_engine.cli_style import section, animate
    lines = ["", section("Indexing " + _safe_cwd().name)]
    animate(lines)
    asyncio.run(_run_index(config, project_dir, full=full, target_path=path, verbose=verbose))


@main.command()
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--oneline", is_flag=True, help="Single-line status for hooks")
@click.pass_context
def status(ctx: click.Context, output_json: bool, oneline: bool) -> None:
    """Show index status and config."""
    import json as _json
    from importlib.metadata import version as pkg_version
    config = ctx.obj["config"]
    verbose = ctx.obj["verbose"]

    if oneline:
        try:
            ver = pkg_version("code-context-engine")
        except Exception:
            ver = "?"
        project_name = _safe_cwd().name
        storage = project_storage_dir(config, _safe_cwd())
        stats_path = storage / "stats.json"
        chunks = 0
        savings = ""
        try:
            from context_engine.storage.vector_store import VectorStore
            vs = VectorStore(db_path=str(storage / "vectors"))
            chunks = vs.count()
        except Exception:
            pass
        if stats_path.exists():
            try:
                stats = _json.loads(stats_path.read_text())
                q = stats.get("queries", 0)
                full = stats.get("full_file_tokens", 0)
                served = stats.get("served_tokens", 0)
                if q > 0 and full > 0:
                    pct = int((full - served) / full * 100)
                    savings = f" · {pct}% saved over {q} queries"
            except Exception:
                pass
        click.echo(
            f"CCE v{ver} · {project_name} · {chunks} chunks indexed{savings}\n"
            f"USE context_search MCP tool for all code questions. Do NOT use Read/Grep to explore code."
        )
        return

    if output_json:
        out = {
            "storage_path": config.storage_path,
            "compression_level": config.compression_level,
            "resource_profile": config.detect_resource_profile(),
        }
        click.echo(_json.dumps(out, indent=2))
        return

    from context_engine.cli_style import (
        dim, warn, magenta, section, animate,
        CHECK, DOT, BULLET, BULLET_OFF,
    )

    lines: list[str] = []
    lines.append("")
    lines.append(section("Status · " + _safe_cwd().name))
    lines.append("")
    lines.append(f"    {BULLET} {label('Storage')}       {value(config.storage_path)}")
    lines.append(f"    {BULLET} {label('Compression')}   {value(config.compression_level)}")
    lines.append(f"    {BULLET} {label('Profile')}       {value(config.detect_resource_profile())}")

    # Embedding model
    model_name = getattr(config, "embedding_model", "BAAI/bge-small-en-v1.5")
    lines.append(f"    {BULLET} {label('Embedding')}     {magenta(model_name)}")

    # Ollama status
    ollama_status = warn("not running")
    compression_mode = "truncation (signatures + docstrings)"
    ollama_bullet = BULLET_OFF
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        if resp.status_code == 200:
            ollama_model = getattr(config, "compression_model", "phi3:mini")
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            if any(ollama_model in m for m in models):
                ollama_status = success("running") + dim(f" ({ollama_model})")
                compression_mode = f"LLM summarization via {ollama_model}"
                ollama_bullet = BULLET
            else:
                ollama_status = success("running") + dim(f" (model {ollama_model} not found)")
    except Exception:
        pass
    lines.append(f"    {ollama_bullet} {label('Ollama')}        {ollama_status}")
    lines.append(f"    {BULLET} {label('Compress')}      {value(compression_mode)}")

    # Token savings
    stats_path = project_storage_dir(config, _safe_cwd()) / "stats.json"
    lines.append("")
    lines.append(section("Token Savings"))
    lines.append("")
    if stats_path.exists():
        try:
            stats = _json.loads(stats_path.read_text())
            raw = stats.get("raw_tokens", 0)
            full = stats.get("full_file_tokens", 0)
            served = stats.get("served_tokens", 0)
            queries = stats.get("queries", 0)
            baseline = max(full, raw) if full > 0 else raw
            saved = max(0, baseline - served) if queries > 0 else 0
            pct = int(saved / baseline * 100) if baseline > 0 and queries > 0 else 0
            lines.append(f"    {dim('Queries:')}        {value(f'{queries:,}')}")
            lines.append(f"    {dim('Full codebase:')}  {value(f'{baseline:,}')} {dim('tokens')}")
            lines.append(f"    {dim('Served:')}         {value(f'{served:,}')} {dim('tokens')}")
            lines.append(f"    {CHECK} {success(f'Saved: {saved:,} tokens ({pct}%)')}")
        except (KeyError, _json.JSONDecodeError):
            lines.append(f"    {DOT} {dim('Error reading stats')}")
    else:
        storage_dir = project_storage_dir(config, _safe_cwd())
        vectors_dir = storage_dir / "vectors"
        if not vectors_dir.exists():
            lines.append(f"    {DOT} {dim('Project not indexed yet')}  {label('cce init')}")
        else:
            lines.append(f"    {DOT} {dim('No usage recorded yet')}  {dim('run context_search via MCP')}")

    # Embedding cache stats — surfaces how much the cache is actually saving.
    cache_db = project_storage_dir(config, _safe_cwd()) / "embedding_cache.db"
    if cache_db.exists():
        try:
            from context_engine.indexer.embedding_cache import EmbeddingCache
            _cache = EmbeddingCache(cache_db)
            try:
                cache_size = _cache.size()
            finally:
                _cache.close()
            db_size_mb = cache_db.stat().st_size / (1024 * 1024)
            lines.append("")
            lines.append(section("Embedding Cache"))
            lines.append("")
            lines.append(f"    {BULLET} {label('Cached embeddings')}  {value(f'{cache_size:,}')}")
            lines.append(f"    {BULLET} {label('Cache size')}         {value(f'{db_size_mb:.1f} MB')}")
        except Exception:
            lines.append("")
            lines.append(f"    {DOT} {dim('Error reading embedding cache')}")

    if verbose:
        storage_path = Path(config.storage_path)
        if storage_path.exists():
            projects = [d for d in storage_path.iterdir() if d.is_dir()]
            lines.append("")
            lines.append(section("Projects Indexed"))
            lines.append("")
            for project in projects:
                chunks_count = len(list(project.glob("**/*.json")))
                lines.append(f"    {dim('·')} {value(project.name)}  {dim(f'{chunks_count} files')}")
        else:
            lines.append(f"    {DOT} {dim('Storage directory does not exist yet.')}")

    lines.append("")
    animate(lines)


@main.command("list")
def list_commands() -> None:
    """Show all available CCE commands with usage examples."""
    from context_engine.cli_style import dim, section, animate

    groups = [
        ("Setup", [
            ("cce init [--agent auto|all|...]", "Index project and register MCP config"),
            ("cce index", "Re-index changed files"),
            ("cce index --full", "Force full re-index of every file"),
            ("cce index --path <file>", "Index one file or directory"),
        ]),
        ("Status & Savings", [
            ("cce status", "Index health, config, embedding model, Ollama status"),
            ("cce status --json", "Machine-readable output"),
            ("cce savings", "Token savings report with visual grid"),
            ("cce savings --all", "Savings across every indexed project"),
            ("cce savings --json", "Machine-readable savings output"),
        ]),
        ("Index Management", [
            ("cce clear", "Clear all index data (asks for confirmation)"),
            ("cce clear --yes", "Skip confirmation"),
            ("cce prune", "Remove data for deleted projects"),
            ("cce prune --dry-run", "Preview without deleting"),
        ]),
        ("Services", [
            ("cce services", "Show status of Ollama, dashboard, MCP"),
            ("cce services start", "Start Ollama + dashboard"),
            ("cce services start ollama", "Start only Ollama"),
            ("cce services start dashboard", "Start dashboard on default port"),
            ("cce services stop", "Stop everything CCE started"),
        ]),
        ("Dashboard", [
            ("cce dashboard", "Open web dashboard in browser"),
            ("cce dashboard --port 8080", "Custom port"),
            ("cce dashboard --no-browser", "Server only, no browser open"),
        ]),
        ("Project Commands", [
            ("cce commands list", "Show all rules, preferences, and hooks"),
            ("cce commands add-rule '<rule>'", "Add a project rule"),
            ("cce commands remove-rule '<rule>'", "Remove a rule"),
            ("cce commands set-pref <key> <val>", "Set a preference"),
            ("cce commands remove-pref <key>", "Remove a preference"),
            ("cce commands add <hook> '<cmd>'", "Add to before_push / before_commit / on_start"),
            ("cce commands remove <hook> '<cmd>'", "Remove from a hook"),
            ("cce commands add-custom <n> '<c>'", "Add a named custom command"),
        ]),
        ("Search", [
            ("cce search '<query>'", "Run a test query and update savings stats"),
            ("cce search '<query>' --top-k 10", "Return more results"),
        ]),
        ("Shortcuts", [
            ("cce start", "Start all services (Ollama + dashboard)"),
            ("cce stop", "Stop all services"),
            ("cce start ollama", "Start only Ollama"),
            ("cce stop dashboard", "Stop only dashboard"),
        ]),
        ("Lifecycle", [
            ("cce init", "Install CCE in project"),
            ("cce upgrade", "Upgrade CCE and refresh project config"),
            ("cce upgrade --check", "Check install method without upgrading"),
            ("cce uninstall", "Remove CCE from project (hooks, MCP, CLAUDE.md)"),
            ("cce serve", "Start MCP server (used by Claude Code)"),
        ]),
        ("Other", [
            ("cce list", "This command"),
            ("cce --version", "Show version"),
            ("cce --help", "Show help"),
        ]),
    ]

    lines: list[str] = [""]
    for group_name, cmds in groups:
        lines.append(section(group_name))
        for cmd, desc in cmds:
            # Align descriptions at column 36
            pad = max(1, 36 - len(cmd))
            lines.append(f"    {label(cmd)}{' ' * pad}{dim(desc)}")
        lines.append("")

    animate(lines)


@main.group()
def commands():
    """Manage project-specific commands (before_push, before_commit, etc.)."""


@commands.command("add")
@click.argument("hook", type=click.Choice(["before_push", "before_commit", "on_start"]))
@click.argument("command")
def commands_add(hook: str, command: str) -> None:
    """Add a command to a hook. Example: cce commands add before_push 'composer test'"""
    from context_engine.project_commands import load_project_only, add_command
    from context_engine.cli_style import warn, CHECK, DOT
    existing = load_project_only(str(_safe_cwd())).get(hook, [])
    if command in existing:
        click.echo(f"  {DOT} {warn('Already exists')} in {hook}: {command}")
        return
    add_command(str(_safe_cwd()), hook, command)
    click.echo(f"  {CHECK} {success('Added')} to {hook}: {command}")


@commands.command("add-custom")
@click.argument("name")
@click.argument("command")
def commands_add_custom(name: str, command: str) -> None:
    """Add a named custom command. Example: cce commands add-custom deploy 'kubectl apply -f k8s/'"""
    from context_engine.project_commands import add_custom_command
    from context_engine.cli_style import CHECK
    add_custom_command(str(_safe_cwd()), name, command)
    click.echo(f"  {CHECK} {success('Added')} custom command '{name}': {command}")


@commands.command("remove")
@click.argument("hook")
@click.argument("command")
def commands_remove(hook: str, command: str) -> None:
    """Remove a command from a hook."""
    from context_engine.project_commands import remove_command
    from context_engine.cli_style import warn, CHECK, DOT
    if remove_command(str(_safe_cwd()), hook, command):
        click.echo(f"  {CHECK} {success('Removed')} from {hook}: {command}")
    else:
        click.echo(f"  {DOT} {warn('Not found')} in {hook}: {command}")


@commands.command("add-rule")
@click.argument("rule")
def commands_add_rule(rule: str) -> None:
    """Add a project rule. Example: cce commands add-rule 'Never use down() in migrations'"""
    from context_engine.project_commands import load_project_only, add_rule
    existing = load_project_only(str(_safe_cwd())).get("rules", [])
    from context_engine.cli_style import warn, CHECK, DOT
    if rule in existing:
        click.echo(f"  {DOT} {warn('Already exists')}: {rule}")
        return
    add_rule(str(_safe_cwd()), rule)
    click.echo(f"  {CHECK} {success('Rule added')}: {rule}")


@commands.command("remove-rule")
@click.argument("rule")
def commands_remove_rule(rule: str) -> None:
    """Remove a project rule."""
    from context_engine.project_commands import remove_rule
    from context_engine.cli_style import warn, CHECK, DOT
    if remove_rule(str(_safe_cwd()), rule):
        click.echo(f"  {CHECK} {success('Rule removed')}: {rule}")
    else:
        click.echo(f"  {DOT} {warn('Not found')}: {rule}")


@commands.command("set-pref")
@click.argument("key")
@click.argument("value")
def commands_set_pref(key: str, value: str) -> None:
    """Set a preference. Example: cce commands set-pref database PostgreSQL"""
    from context_engine.project_commands import set_preference
    from context_engine.cli_style import CHECK
    set_preference(str(_safe_cwd()), key, value)
    click.echo(f"  {CHECK} {success('Preference set')}: {key} = {value}")


@commands.command("remove-pref")
@click.argument("key")
def commands_remove_pref(key: str) -> None:
    """Remove a preference."""
    from context_engine.project_commands import remove_preference
    from context_engine.cli_style import warn, CHECK, DOT
    if remove_preference(str(_safe_cwd()), key):
        click.echo(f"  {CHECK} {success('Preference removed')}: {key}")
    else:
        click.echo(f"  {DOT} {warn('Not found')}: {key}")


@commands.command("list")
def commands_list() -> None:
    """Show all project commands, rules, and preferences (merged with workspace)."""
    from context_engine.project_commands import load_commands
    from context_engine.cli_style import dim, section, animate, DOT, BULLET

    cmds = load_commands(str(_safe_cwd()))
    lines: list[str] = [""]

    if not cmds:
        lines.append(section("Project Commands"))
        lines.append("")
        lines.append(f"    {DOT} {dim('No project configuration found.')}")
        lines.append("")
        lines.append(f"    {dim('Try:')}  {label('cce commands add-rule')} {dim(chr(39))}Never use down(){dim(chr(39))}")
        lines.append(f"           {label('cce commands set-pref')} {dim('database PostgreSQL')}")
        lines.append(f"           {label('cce commands add')} {dim('before_push')} {dim(chr(39))}composer test{dim(chr(39))}")
        lines.append("")
        animate(lines)
        return

    rules = cmds.get("rules", [])
    prefs = cmds.get("preferences", {})
    hooks = {k: v for k, v in cmds.items() if k not in ("rules", "preferences", "custom") and isinstance(v, list)}
    custom = cmds.get("custom", {})

    if rules:
        lines.append(section("Rules"))
        for r in rules:
            lines.append(f"    {ARROW} {r}")
        lines.append("")
    if prefs:
        lines.append(section("Preferences"))
        for k, v in prefs.items():
            pad = max(1, 18 - len(k))
            lines.append(f"    {label(k)}{' ' * pad}{value(str(v))}")
        lines.append("")
    hook_labels = {"before_push": "Before push", "before_commit": "Before commit", "on_start": "On start"}
    for hook_key, hook_cmds in hooks.items():
        hook_name = hook_labels.get(hook_key, hook_key)
        lines.append(section(hook_name))
        for c in hook_cmds:
            lines.append(f"    {BULLET} {dim('$')} {value(c)}")
        lines.append("")
    if custom:
        lines.append(section("Custom Commands"))
        for name, cmd in custom.items():
            pad = max(1, 14 - len(name))
            lines.append(f"    {label(name)}{' ' * pad}{ARROW} {dim('$')} {value(cmd)}")
        lines.append("")

    animate(lines)


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--all", "all_projects", is_flag=True, help="Show savings for all indexed projects")
@click.pass_context
def savings(ctx: click.Context, as_json: bool, all_projects: bool) -> None:
    """Show token savings report — how much CCE is saving you."""
    config = ctx.obj["config"]
    _run_savings_report(config, as_json=as_json, all_projects=all_projects)


def _run_savings_report(config, *, as_json: bool = False, all_projects: bool = False) -> None:
    """Shared implementation for savings report (used by subcommand and shortcut)."""
    import json as _json

    storage_root = Path(config.storage_path)

    def _load_stats(project_dir: Path) -> dict | None:
        stats_path = project_dir / "stats.json"
        if not stats_path.exists():
            return None
        try:
            return _json.loads(stats_path.read_text())
        except (KeyError, _json.JSONDecodeError):
            return None

    def _load_buckets(project_dir: Path) -> tuple[dict, dict]:
        """Open memory.db and pull per-bucket savings + the
        output_compression level histogram. Falls back to bucket data
        embedded in stats.json if memory.db is missing or empty.
        Returns ({bucket: {baseline, served, calls}}, {level: count}).
        """
        from context_engine.memory import db as _memory_db
        db_path = project_dir / "memory.db"
        empty = {b: {"baseline": 0, "served": 0, "calls": 0} for b in _memory_db.BUCKETS}

        # Try memory.db first
        if db_path.exists():
            try:
                conn = _memory_db.connect(db_path)
                try:
                    buckets = _memory_db.aggregate_savings(conn)
                    levels = _memory_db.aggregate_output_compression_levels(conn)
                    # Only use if there's actual data
                    total = sum(int(v.get("baseline", 0)) for v in buckets.values())
                    if total > 0:
                        return buckets, levels
                finally:
                    conn.close()
            except Exception:
                pass

        # Fall back to bucket data embedded in stats.json
        stats = _load_stats(project_dir)
        if stats and "buckets" in stats:
            buckets = {}
            for key, val in stats["buckets"].items():
                buckets[key] = {
                    "baseline": int(val.get("baseline", 0)),
                    "served": int(val.get("served", 0)),
                    "calls": int(val.get("calls", 0)),
                }
            total = sum(v["baseline"] for v in buckets.values())
            if total > 0:
                return buckets, {}

        return empty, {}

    from context_engine.cli_style import dim, bold
    from context_engine.pricing import get_model_pricing

    _all_pricing = get_model_pricing()
    _pricing_model = config.pricing_model.lower()
    _default = _all_pricing.get("opus", {"input": 15.0, "output": 75.0})
    _model_pricing = _all_pricing.get(_pricing_model, _default)
    _input_price_per_m = _model_pricing["input"]
    _output_price_per_m = _model_pricing["output"]
    _INPUT_COST = _input_price_per_m / 1_000_000
    _OUTPUT_COST = _output_price_per_m / 1_000_000
    _model_label = _pricing_model.capitalize()
    _GRID_COLS = 10
    _FILLED = "⛁"
    _EMPTY = "⛶"

    # The output_compression bucket is the only one saving output tokens.
    _OUTPUT_BUCKETS = {"output_compression"}

    def _fmt_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def _fmt_cost_input(n: int) -> str:
        cost = n * _INPUT_COST
        if cost < 0.01:
            return "<$0.01"
        return f"${cost:.2f}"

    def _fmt_cost_output(n: int) -> str:
        cost = n * _OUTPUT_COST
        if cost < 0.01:
            return "<$0.01"
        return f"${cost:.2f}"

    def _bucket_cost(bucket: str, tokens: int) -> float:
        rate = _OUTPUT_COST if bucket in _OUTPUT_BUCKETS else _INPUT_COST
        return tokens * rate

    def _fmt_cost_raw(amount: float) -> str:
        if amount < 0.01:
            return "<$0.01"
        return f"${amount:.2f}"

    def _bar(saved_pct: int) -> str:
        """Render ⛁ ⛁ ⛁ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ grid where filled = tokens used."""
        used_pct = 100 - saved_pct
        filled = max(1, min(_GRID_COLS, round(used_pct / 100 * _GRID_COLS)))
        cells = []
        for i in range(_GRID_COLS):
            if i < filled:
                cells.append(click.style(_FILLED, fg="cyan"))
            else:
                cells.append(click.style(_EMPTY, dim=True))
        return " ".join(cells)

    # Bucket display metadata. Order = render order. `estimate=True` adds a
    # trailing asterisk and a footnote so users know it's a counterfactual.
    _BUCKET_DISPLAY = [
        ("retrieval",            "retrieval",             False),
        ("chunk_compression",    "chunk compression",     False),
        ("output_compression",   "output compression",    True),
        ("memory_recall",        "memory recall",         False),
        ("grammar",              "grammar",               False),
        ("turn_summarization",   "turn summarization",    False),
        ("progressive_disclosure", "progressive disclosure", True),
    ]

    def _bucket_totals(buckets: dict) -> tuple[int, int]:
        """Sum baseline/served across all buckets."""
        b = sum(int(v.get("baseline", 0)) for v in buckets.values())
        s = sum(int(v.get("served", 0)) for v in buckets.values())
        return b, s

    def _split_io(buckets: dict) -> tuple[int, int, int, int]:
        """Split buckets into (input_baseline, input_served, output_baseline, output_served)."""
        ib = is_ = ob = os_ = 0
        for key, v in buckets.items():
            base = int(v.get("baseline", 0))
            srv = int(v.get("served", 0))
            if key in _OUTPUT_BUCKETS:
                ob += base
                os_ += srv
            else:
                ib += base
                is_ += srv
        return ib, is_, ob, os_

    def _print_project(name: str, stats: dict, buckets: dict, levels: dict) -> None:
        queries = stats.get("queries", 0)

        # Prefer canonical bucket totals; fall back to legacy stats.json
        # fields if the project hasn't accumulated any bucket events yet.
        bucket_baseline, bucket_served = _bucket_totals(buckets)
        if bucket_baseline > 0:
            baseline = bucket_baseline
            served = bucket_served
        else:
            full_file = stats.get("full_file_tokens", 0)
            raw = stats.get("raw_tokens", 0)
            served_legacy = stats.get("served_tokens", 0)
            baseline = max(full_file, raw) if full_file > 0 else raw
            served = served_legacy

        tokens_saved = max(0, baseline - served) if queries > 0 else 0
        saved_pct = int(tokens_saved / baseline * 100) if baseline > 0 and queries > 0 else 0

        # Split into input / output savings
        in_base, in_srv, out_base, out_srv = _split_io(buckets)
        in_saved = max(0, in_base - in_srv)
        out_saved = max(0, out_base - out_srv)
        # Legacy projects have no bucket data; treat all savings as input.
        if bucket_baseline == 0 and tokens_saved > 0:
            in_saved = tokens_saved
            out_saved = 0
        total_cost_saved = in_saved * _INPUT_COST + out_saved * _OUTPUT_COST

        q_label = "query" if queries == 1 else "queries"

        click.echo()
        click.echo(f"  {bold(name)} {dim('·')} {value(str(queries))} {dim(q_label)}")
        click.echo()

        # Show friendly message when no searches have happened yet.
        # Exception: bucket data with real savings means context_search
        # was called but the legacy query counter wasn't incremented.
        has_bucket_savings = bucket_baseline > 0 and bucket_served < bucket_baseline
        if queries == 0 and not has_bucket_savings:
            click.echo(f"  {dim('Waiting for first search.')}")
            click.echo(f"  {dim('Stats populate after context_search calls via MCP.')}")
            click.echo()
            return

        # Headline bar + percentage
        click.echo(
            f"  {_bar(saved_pct)}  "
            f"{click.style(f'{saved_pct}%', fg='green', bold=True)} "
            f"{dim('tokens saved')}"
        )
        click.echo()

        # Input / output / total saved
        click.echo(
            f"  {dim('Input savings')}   "
            f"{value(_fmt_tokens(in_saved)):>10}  {dim('tokens')}   "
            f"{dim(_fmt_cost_input(in_saved))}"
        )
        if out_saved > 0:
            click.echo(
                f"  {dim('Output savings')}  "
                f"{value(_fmt_tokens(out_saved)):>10}  {dim('tokens')}   "
                f"{dim(_fmt_cost_output(out_saved))}"
            )
        click.echo(f"  {dim('─' * 42)}")
        click.echo(
            f"  {success('Total saved')}   "
            f"{click.style(_fmt_tokens(tokens_saved), fg='green', bold=True):>10}  {dim('tokens')}   "
            f"{click.style(_fmt_cost_raw(total_cost_saved), fg='green', bold=True)}"
        )
        # Per-query average
        if queries > 0 and tokens_saved > 0:
            avg_tokens = tokens_saved // max(1, queries)
            avg_cost = _fmt_cost_raw(total_cost_saved / max(1, queries))
            click.echo(
                f"  {dim(f'~{_fmt_tokens(avg_tokens)} tokens / query')}  "
                f"{dim(f'~{avg_cost} / query')}"
            )
        click.echo()

        # Per-bucket breakdown — only rows with non-zero savings render.
        # Definition order is preserved for ties (Python's sort is stable).
        rows = []
        for idx, (key, display, is_est) in enumerate(_BUCKET_DISPLAY):
            b = buckets.get(key, {"baseline": 0, "served": 0, "calls": 0})
            base = int(b.get("baseline", 0))
            srv = int(b.get("served", 0))
            saved = max(0, base - srv)
            if saved <= 0:
                continue
            pct = int(saved / baseline * 100) if baseline > 0 else 0
            rows.append((key, display, pct, saved, int(b.get("calls", 0)), is_est, idx))
        # Polish 2: sort by saved tokens descending. Biggest wins first.
        rows.sort(key=lambda r: (-r[3], r[6]))

        if rows:
            click.echo(f"  {dim('Breakdown:')}")
            # Polish 5: glue the asterisk to the label so the percentage column
            # stays straight. Compute label_width over the asterisk-suffixed
            # form so estimate buckets don't blow out the alignment.
            displayed_labels = [
                f"{display}*" if is_est else display
                for _, display, _, _, _, is_est, _ in rows
            ]
            label_width = max(len(s) for s in displayed_labels) + 1
            # Polish 3: normalize bar fill against the largest bucket's saved
            # tokens, not the total. Otherwise a dominant bucket squashes all
            # others to 0–1 cells and the visualisation goes blind.
            max_saved = max(r[3] for r in rows)
            any_estimate = False
            for key, display, pct, saved, calls, is_est in [
                (k, d, p, s, c, e) for k, d, p, s, c, e, _ in rows
            ]:
                if is_est:
                    any_estimate = True
                # Polish 1: never round non-zero savings down to "0%".
                if saved > 0 and pct < 1:
                    pct_text = "<1%".rjust(4)
                else:
                    pct_text = f"{pct}%".rjust(4)
                # Polish 3: bar fill ∝ saved / max_saved, not pct / 100.
                ratio = saved / max_saved if max_saved > 0 else 0
                fill = max(1, min(_GRID_COLS, round(ratio * _GRID_COLS))) if saved > 0 else 0
                mini_bar = (
                    click.style("▰" * fill, fg="cyan")
                    + click.style("▱" * (_GRID_COLS - fill), dim=True)
                )
                # Polish 4: singular "1 call" / plural "N calls".
                call_text = "1 call" if calls == 1 else f"{calls} calls"
                # Polish 5: asterisk glued to label, no separate marker column.
                label_text = f"{display}*" if is_est else display
                cost_str = _fmt_cost_raw(_bucket_cost(key, saved))
                click.echo(
                    f"    {label(label_text.ljust(label_width))}  "
                    f"{value(pct_text)}  {mini_bar}  "
                    f"{dim(_fmt_tokens(saved).rjust(6))} "
                    f"{dim(cost_str.rjust(8))} "
                    f"{dim(f'· {call_text}')}"
                )
            click.echo()
            if any_estimate:
                from context_engine.compression.output_rules import (
                    ESTIMATED_AVG_REPLY_TOKENS as _EST_REPLY,
                )
                click.echo(
                    "  " + dim(
                        f"* estimated. output compression assumes a "
                        f"{_EST_REPLY}-token avg reply; "
                        "progressive disclosure compares against full payload dump."
                    )
                )
            if levels:
                lv = ", ".join(f"{k}={v}" for k, v in sorted(levels.items()))
                click.echo(f"  {dim(f'Output compression levels seen: {lv}')}")
        else:
            # Legacy fallback: project has stats.json but no per-bucket data
            # yet (older deployment, or memory.db not present). Compute the
            # retrieval / chunk-compression split from legacy fields so
            # users still see *some* breakdown.
            full_file_legacy = stats.get("full_file_tokens", 0)
            raw_legacy = stats.get("raw_tokens", 0)
            served_legacy = stats.get("served_tokens", 0)
            retrieval_pct = (
                int(round((1 - raw_legacy / full_file_legacy) * 100))
                if full_file_legacy > 0 and raw_legacy <= full_file_legacy
                else 0
            )
            compression_pct = (
                int(round((1 - served_legacy / raw_legacy) * 100))
                if raw_legacy > 0 and served_legacy <= raw_legacy
                else 0
            )
            click.echo(
                f"  {dim('How:')}  "
                f"{label('retrieval')} {value(f'{max(0, retrieval_pct)}%')}"
                f"  {dim('+')}  "
                f"{label('compression')} {value(f'{max(0, compression_pct)}%')}"
            )

        pricing_note = (
            f"Cost estimate based on {_model_label} pricing "
            f"(input ${_input_price_per_m}/1M, output ${_output_price_per_m}/1M)"
        )
        click.echo(f"  {dim(pricing_note)}")

    def _json_entry(name: str, stats: dict, buckets: dict, levels: dict) -> dict:
        full_file = stats.get("full_file_tokens", 0)
        raw = stats.get("raw_tokens", 0)
        served = stats.get("served_tokens", 0)
        bucket_baseline, bucket_served = _bucket_totals(buckets)
        if bucket_baseline > 0:
            baseline = bucket_baseline
            served_total = bucket_served
        else:
            baseline = max(full_file, raw) if full_file > 0 else raw
            served_total = served
        saved = max(0, baseline - served_total)
        in_base, in_srv, out_base, out_srv = _split_io(buckets)
        in_saved = max(0, in_base - in_srv)
        out_saved = max(0, out_base - out_srv)
        retrieval_pct = (
            int(round((1 - raw / full_file) * 100))
            if full_file > 0 and raw <= full_file
            else 0
        )
        compression_pct = (
            int(round((1 - served / raw) * 100))
            if raw > 0 and served <= raw
            else 0
        )
        return {
            "project": name,
            "queries": stats.get("queries", 0),
            "full_file_tokens": full_file,
            "raw_tokens": raw,
            "served_tokens": served,
            "tokens_saved": saved,
            "input_tokens_saved": in_saved,
            "output_tokens_saved": out_saved,
            # Kept for backward compat with anything scraping this JSON:
            "savings_pct": int(saved / baseline * 100) if baseline > 0 else 0,
            "retrieval_savings_pct": max(0, retrieval_pct),
            "compression_savings_pct": max(0, compression_pct),
            # New per-bucket breakdown.
            "buckets": buckets,
            "output_compression_levels": levels,
        }

    # Collect projects
    if all_projects:
        if not storage_root.exists():
            if as_json:
                click.echo(_json.dumps({"projects": []}))
            else:
                click.echo("No indexed projects found.")
            return
        project_dirs = sorted(
            (d for d in storage_root.iterdir() if d.is_dir()),
            key=lambda d: d.name,
        )
    else:
        project_dirs = [project_storage_dir(config, _safe_cwd())]

    # Each report carries its bucket totals and level histogram alongside
    # the legacy stats.json so downstream renderers/JSON emitters can
    # pick the canonical source.
    reports: list[tuple[str, dict, dict, dict]] = []
    for pd in project_dirs:
        stats = _load_stats(pd)
        buckets, levels = _load_buckets(pd)
        bucket_baseline = sum(int(v.get("baseline", 0)) for v in buckets.values())
        if stats is not None or bucket_baseline > 0:
            reports.append((pd.name, stats or {
                "queries": 0, "raw_tokens": 0, "served_tokens": 0,
                "full_file_tokens": 0,
            }, buckets, levels))

    if not reports:
        if as_json:
            if all_projects:
                click.echo(_json.dumps({"projects": []}))
            else:
                empty_buckets = {b: {"baseline": 0, "served": 0, "calls": 0}
                                 for b in __import__(
                                     "context_engine.memory.db", fromlist=["BUCKETS"],
                                 ).BUCKETS}
                click.echo(_json.dumps(_json_entry(_safe_cwd().name, {
                    "raw_tokens": 0, "served_tokens": 0, "queries": 0,
                }, empty_buckets, {})))
        else:
            click.echo(f"  {dim('No usage recorded yet.')}")
            click.echo(f"  {dim('Run context_search queries via MCP to start tracking savings.')}")
        return

    if as_json:
        if all_projects:
            click.echo(_json.dumps(
                {"projects": [_json_entry(n, s, b, lv) for n, s, b, lv in reports]},
                indent=2,
            ))
        else:
            click.echo(_json.dumps(_json_entry(*reports[0]), indent=2))
        return

    # Text output
    for name, stats, buckets, levels in reports:
        _print_project(name, stats, buckets, levels)
        if len(reports) > 1:
            click.echo()
            click.echo("  " + "─" * 52)

    if len(reports) > 1:
        # Prefer canonical bucket totals; fall back to legacy fields.
        def _proj_baseline(s, b):
            bt = sum(int(v.get("baseline", 0)) for v in b.values())
            if bt > 0:
                return bt
            ff = s.get("full_file_tokens", 0)
            r = s.get("raw_tokens", 0)
            return max(ff, r) if ff > 0 else r
        def _proj_served(s, b):
            bt = sum(int(v.get("served", 0)) for v in b.values())
            if bt > 0:
                return bt
            return s.get("served_tokens", 0)
        total_baseline = sum(_proj_baseline(s, b) for _, s, b, _ in reports)
        total_served = sum(_proj_served(s, b) for _, s, b, _ in reports)
        total_queries = sum(s.get("queries", 0) for _, s, _, _ in reports)
        total_saved = max(0, total_baseline - total_served)
        total_pct = int(total_saved / total_baseline * 100) if total_baseline > 0 else 0
        # Aggregate input/output across all projects
        all_in_saved = all_out_saved = 0
        for _, stats, bkts, _ in reports:
            ib, is_, ob, os_ = _split_io(bkts)
            all_in_saved += max(0, ib - is_)
            all_out_saved += max(0, ob - os_)
        # Legacy projects with no bucket data: attribute remaining to input
        bucket_total_saved = all_in_saved + all_out_saved
        if bucket_total_saved < total_saved:
            all_in_saved += total_saved - bucket_total_saved
        agg_cost = all_in_saved * _INPUT_COST + all_out_saved * _OUTPUT_COST
        click.echo()
        click.echo(
            f"  {bold('Total')} {dim('across')} {value(str(len(reports)))} "
            f"{dim('projects ·')} {value(f'{total_queries:,}')} {dim('queries')}"
        )
        click.echo(
            f"  {_bar(total_pct)}  "
            f"{click.style(f'{total_pct}%', fg='green', bold=True)} "
            f"{dim('saved ·')} "
            f"{click.style(_fmt_tokens(total_saved), fg='green', bold=True)} "
            f"{dim('tokens ·')} "
            f"{click.style(_fmt_cost_raw(agg_cost), fg='green', bold=True)}"
        )

    click.echo()


@main.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def clear(ctx: click.Context, yes: bool) -> None:
    """Clear all index data for the current project (vectors, FTS, graph, manifest)."""
    from context_engine.storage.local_backend import LocalBackend
    from context_engine.cli_style import warn, dim, section, animate, CHECK, DOT

    config = ctx.obj["config"]
    project_name = _safe_cwd().name
    storage_dir = project_storage_dir(config, _safe_cwd())

    if not storage_dir.exists():
        animate(["", f"  {DOT} {dim('No index data found for')} {value(project_name)}", ""])
        return

    if not yes:
        click.echo("")
        click.echo(section("Clear Index"))
        click.echo("")
        click.confirm(f"    {warn('Delete all index data for')} {value(project_name)}?", abort=True)

    backend = LocalBackend(base_path=str(storage_dir))
    asyncio.run(backend.clear())

    manifest_path = storage_dir / "manifest.json"
    if manifest_path.exists():
        manifest_path.write_text(json.dumps({"__schema_version": 2, "files": {}}))

    stats_path = storage_dir / "stats.json"
    stats_path.write_text(json.dumps({"queries": 0, "raw_tokens": 0, "served_tokens": 0, "full_file_tokens": 0}))

    animate([
        "",
        f"    {CHECK} {success('Cleared')} index data for {value(project_name)}",
        f"    {dim('Run')} {click.style('cce index', fg='cyan')} {dim('to rebuild')}",
        "",
    ])


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be removed without deleting")
@click.pass_context
def prune(ctx: click.Context, dry_run: bool) -> None:
    """Remove index data for projects whose directories no longer exist."""
    import shutil
    from context_engine.cli_style import warn, dim, section, animate, CHECK, CROSS, DOT

    config = ctx.obj["config"]
    storage_root = Path(config.storage_path)
    if not storage_root.exists():
        animate(["", f"  {DOT} {dim('No indexed projects found.')}", ""])
        return

    removed = []
    kept = []
    for project_dir in sorted(storage_root.iterdir()):
        if not project_dir.is_dir():
            continue
        meta_path = project_dir / "meta.json"
        if not meta_path.exists():
            kept.append((project_dir.name, "(no meta.json)"))
            continue
        try:
            meta = json.loads(meta_path.read_text())
            source_path = Path(meta.get("project_dir", ""))
        except (json.JSONDecodeError, OSError):
            kept.append((project_dir.name, "(unreadable meta.json)"))
            continue

        if source_path and source_path.exists():
            kept.append((project_dir.name, str(source_path)))
        else:
            removed.append((project_dir.name, str(source_path), project_dir))

    lines: list[str] = []
    lines.append("")
    lines.append(section("Prune" + (" (dry run)" if dry_run else "")))
    lines.append("")

    if not removed:
        lines.append(f"    {CHECK} {success('Nothing to prune')}  all indexed projects still exist")
        lines.append("")
        for name, path in kept:
            lines.append(f"    {CHECK} {value(name)}  {dim(path)}")
        lines.append("")
        animate(lines)
        return

    for name, path, storage_dir in removed:
        if dry_run:
            lines.append(f"    {DOT} {warn('would remove')}  {value(name)}  {dim(path)}")
        else:
            shutil.rmtree(storage_dir)
            lines.append(f"    {CROSS} {warn('removed')}      {value(name)}  {dim(path)}")

    for name, path in kept:
        lines.append(f"    {CHECK} {dim('kept')}          {value(name)}  {dim(path)}")

    lines.append("")
    animate(lines)


@main.command()
@click.argument("query")
@click.option("--top-k", default=5, show_default=True, help="Number of results")
@click.pass_context
def search(ctx: click.Context, query: str, top_k: int) -> None:
    """Run a test search query and show results (also updates savings stats)."""
    from context_engine.cli_style import section, animate, dim, CHECK, DOT

    config = ctx.obj["config"]
    project_dir = str(_safe_cwd())

    async def _search():
        from context_engine.storage.local_backend import LocalBackend
        from context_engine.indexer.embedder import Embedder
        from context_engine.retrieval.retriever import HybridRetriever

        storage_dir = project_storage_dir(config, _safe_cwd())
        if not (storage_dir / "vectors").exists():
            animate(["", f"  {DOT} {dim('Not indexed yet. Run:')} {label('cce init')}", ""])
            return

        backend = LocalBackend(base_path=str(storage_dir))
        embedder = Embedder(model_name=config.embedding_model)
        retriever = HybridRetriever(backend=backend, embedder=embedder)
        results = await retriever.retrieve(query, top_k=top_k)

        # Filter out CCE config/editor files (same filter as MCP server)
        from context_engine.integration.mcp_server import _is_cce_config
        results = [r for r in results if not _is_cce_config(r.file_path)]

        lines: list[str] = []
        lines.append("")
        lines.append(section(f"Search · {query}"))
        lines.append("")

        if not results:
            lines.append(f"    {DOT} {dim('No results found')}")
        else:
            # Compute tokens per file, capping served at full-file size to
            # handle overlapping chunks (e.g. class + method from same file).
            per_file_served: dict[str, int] = {}
            for r in results:
                chunk_tokens = max(1, len(r.content) // 4)
                per_file_served[r.file_path] = per_file_served.get(r.file_path, 0) + chunk_tokens

            # Estimate full file tokens and cap served per file
            full_file_tokens = 0
            served_tokens = 0
            for fp, raw_served in per_file_served.items():
                full_path = Path(project_dir) / fp
                try:
                    file_tokens = max(1, len(full_path.read_text(errors="ignore")) // 4)
                except OSError:
                    file_tokens = raw_served
                full_file_tokens += file_tokens
                served_tokens += min(raw_served, file_tokens)

            for i, r in enumerate(results, 1):
                conf = r.metadata.get("confidence", "")
                conf_str = f"  {dim(f'({conf:.2f})')}" if isinstance(conf, (int, float)) else ""
                lines.append(f"    {label(str(i))}. {value(r.file_path)}:{r.start_line}-{r.end_line}{conf_str}")
                # Show first line of content
                first_line = r.content.strip().split("\n")[0][:80]
                lines.append(f"       {dim(first_line)}")

            lines.append("")
            savings_pct = int((1 - served_tokens / full_file_tokens) * 100) if full_file_tokens > 0 else 0
            lines.append(f"    {CHECK} {success(f'{len(results)} results')}  {dim(f'{served_tokens} tokens served vs {full_file_tokens} full file tokens ({savings_pct}% saved)')}")

            # Update stats
            stats_path = storage_dir / "stats.json"
            try:
                stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
            except (json.JSONDecodeError, OSError):
                stats = {}
            stats["queries"] = stats.get("queries", 0) + 1
            stats["full_file_tokens"] = stats.get("full_file_tokens", 0) + full_file_tokens
            stats["served_tokens"] = stats.get("served_tokens", 0) + served_tokens
            stats_path.write_text(json.dumps(stats))

        lines.append("")
        animate(lines)

    asyncio.run(_search())


@main.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def uninstall(yes: bool) -> None:
    """Remove CCE from the current project (hooks, .mcp.json entry, CLAUDE.md block)."""
    from context_engine.cli_style import section, animate, dim, warn, CROSS, DOT

    project_dir = _safe_cwd()
    project_name = project_dir.name

    if not yes:
        if not click.confirm(f"Remove CCE from {project_name}?", default=False):
            click.echo("Cancelled.")
            return

    lines: list[str] = []
    lines.append("")
    lines.append(section(f"Uninstall · {project_name}"))
    lines.append("")

    # Remove git hooks
    hooks_dir = project_dir / ".git" / "hooks"
    removed_hooks = 0
    if hooks_dir.exists():
        for hook_name in ["post-commit", "post-checkout", "post-merge"]:
            hook_file = hooks_dir / hook_name
            if hook_file.exists():
                content = hook_file.read_text()
                if "cce" in content.lower() or "context-engine" in content.lower():
                    hook_file.unlink()
                    removed_hooks += 1
    if removed_hooks:
        lines.append(f"    {CROSS} {warn('Removed')} {removed_hooks} git hooks")
    else:
        lines.append(f"    {DOT} {dim('No CCE git hooks found')}")

    # Remove MCP config from all editors
    from context_engine.editors import EDITORS, INSTRUCTION_FILES, remove_mcp, remove_instruction_file

    for editor_key, editor in EDITORS.items():
        msg = remove_mcp(project_dir, editor_key)
        if msg:
            lines.append(f"    {CROSS} {warn(msg)}")

    # Remove instruction files from non-Claude editors
    for file_key in INSTRUCTION_FILES:
        msg = remove_instruction_file(project_dir, file_key)
        if msg:
            lines.append(f"    {CROSS} {warn(msg)}")

    # Remove CCE block from CLAUDE.md. _extract_existing_cce_block() recognises
    # the current versioned form (<!-- cce-block-version: N --> ... <!-- /cce-block -->),
    # the legacy "## Context Engine (CCE)" heading-only form, AND the older
    # CCE:BEGIN/CCE:END marker pair — keeping uninstall in lockstep with init
    # so the routing instructions don't get left behind.
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text()
        block = _extract_existing_cce_block(content)
        legacy_begin = "<!-- CCE:BEGIN -->"
        legacy_end = "<!-- CCE:END -->"
        if block is not None:
            new_content = content.replace(block, "", 1).strip()
            if new_content:
                claude_md.write_text(new_content + "\n")
            else:
                claude_md.unlink()
            lines.append(f"    {CROSS} {warn('Removed')} CCE block from CLAUDE.md")
        elif legacy_begin in content:
            start = content.index(legacy_begin)
            end = (
                content.index(legacy_end) + len(legacy_end)
                if legacy_end in content
                else len(content)
            )
            new_content = (content[:start] + content[end:]).strip()
            if new_content:
                claude_md.write_text(new_content + "\n")
            else:
                claude_md.unlink()
            lines.append(f"    {CROSS} {warn('Removed')} CCE block from CLAUDE.md")
        elif "context_search" in content or "context-engine" in content.lower():
            lines.append(f"    {DOT} {warn('CLAUDE.md has CCE references but no markers. Edit manually.')}")
        else:
            lines.append(f"    {DOT} {dim('No CCE block in CLAUDE.md')}")
    else:
        lines.append(f"    {DOT} {dim('No CLAUDE.md found')}")

    # Remove .cce directory
    cce_dir = project_dir / ".cce"
    if cce_dir.exists():
        import shutil
        shutil.rmtree(cce_dir)
        lines.append(f"    {CROSS} {warn('Removed')} .cce/ directory")
    else:
        lines.append(f"    {DOT} {dim('No .cce/ directory')}")

    # Remove .context-engine.yaml (per-project config)
    project_config = project_dir / ".context-engine.yaml"
    if project_config.exists():
        project_config.unlink()
        lines.append(f"    {CROSS} {warn('Removed')} .context-engine.yaml")

    # Remove CCE hooks from .claude/settings.local.json AND .claude/settings.json
    for settings_name in ("settings.local.json", "settings.json"):
        settings_path = project_dir / ".claude" / settings_name
        if not settings_path.exists():
            continue
        try:
            data = json.loads(settings_path.read_text())
            hooks = data.get("hooks", {})
            changed = False
            for event in list(hooks.keys()):
                original = hooks[event]
                filtered = [
                    h for h in original
                    if not any(
                        "cce" in cmd.get("command", "")
                        for cmd in (h.get("hooks", []) if isinstance(h, dict) else [])
                    )
                ]
                if len(filtered) != len(original):
                    hooks[event] = filtered
                    changed = True
                # Remove empty hook lists
                if not hooks[event]:
                    del hooks[event]
                    changed = True
            if changed:
                if not hooks:
                    del data["hooks"]
                if data:
                    settings_path.write_text(json.dumps(data, indent=2) + "\n")
                else:
                    settings_path.unlink()
                # Remove empty .claude directory
                claude_dir = project_dir / ".claude"
                if claude_dir.exists() and not any(claude_dir.iterdir()):
                    claude_dir.rmdir()
                lines.append(f"    {CROSS} {warn('Removed')} CCE hooks from .claude/{settings_name}")
        except (json.JSONDecodeError, OSError):
            pass

    # Remove CCE entries from .gitignore (including comment lines)
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".cce" in content or "context-engine" in content.lower() or "cce" in content.lower() or ".claude/settings.local.json" in content:
            # These are the exact entries CCE adds (see project_commands._GITIGNORE_ENTRIES)
            cce_lines = {".cce/", ".claude/settings.local.json"}
            new_lines = [
                line for line in content.splitlines()
                if line.strip() not in cce_lines
                and "context-engine" not in line.lower()
                and not (line.startswith("#") and ("cce" in line.lower() or "claude code local settings" in line.lower()))
            ]
            new_content = "\n".join(new_lines).strip()
            if new_content:
                gitignore.write_text(new_content + "\n")
            else:
                gitignore.unlink()
            lines.append(f"    {CROSS} {warn('Removed')} CCE entries from .gitignore")

    # Remove index data from ~/.cce/projects/<project>
    config = load_config()
    index_dir = project_storage_dir(config, project_dir)
    if index_dir.exists():
        import shutil
        shutil.rmtree(index_dir)
        lines.append(f"    {CROSS} {warn('Removed')} index data from {dim(str(index_dir))}")
    else:
        lines.append(f"    {DOT} {dim('No index data found')}")

    lines.append("")
    animate(lines)


@main.command()
@click.argument("service", required=False, type=click.Choice(["ollama", "dashboard", "all"]), default="all")
@click.option("--port", default=8080, show_default=True, help="Dashboard port")
def start(service: str, port: int) -> None:
    """Start CCE services (shortcut for cce services start)."""
    from context_engine.services import start_ollama, start_dashboard
    from context_engine.cli_style import section, animate, CHECK, DOT

    lines = ["", section("Starting Services")]
    targets = ["ollama", "dashboard"] if service == "all" else [service]
    for target in targets:
        if target == "ollama":
            ok, msg = start_ollama()
        else:
            ok, msg = start_dashboard(port=port)
        prefix = CHECK if ok else DOT
        lines.append(f"    {prefix} {msg}")
    lines.append("")
    animate(lines)


@main.command()
@click.argument("service", required=False, type=click.Choice(["ollama", "dashboard", "all"]), default="all")
def stop(service: str) -> None:
    """Stop CCE services (shortcut for cce services stop)."""
    from context_engine.services import stop_ollama, stop_dashboard
    from context_engine.cli_style import section, animate, CHECK, DOT

    lines = ["", section("Stopping Services")]
    targets = ["ollama", "dashboard"] if service == "all" else [service]
    for target in targets:
        if target == "ollama":
            ok, msg = stop_ollama()
        else:
            ok, msg = stop_dashboard()
        prefix = CHECK if ok else DOT
        lines.append(f"    {prefix} {msg}")
    lines.append("")
    animate(lines)


@main.command()
@click.option("--check", is_flag=True, help="Check for updates without installing")
@click.pass_context
def upgrade(ctx: click.Context, check: bool) -> None:
    """Upgrade code-context-engine to the latest version and refresh project config."""
    import importlib.metadata
    import subprocess
    from context_engine.cli_style import section, animate

    current = importlib.metadata.version("code-context-engine")
    lines = ["", section("Upgrade")]
    lines.append(f"    Current version: {click.style(current, fg='cyan', bold=True)}")

    # Detect install method from the cce binary path
    cce_bin = Path(sys.argv[0]).resolve()
    cce_str = str(cce_bin)

    installer = None
    upgrade_cmd: list[str] = []

    if "/uv/" in cce_str or ".local/share/uv" in cce_str:
        installer = "uv"
        upgrade_cmd = ["uv", "tool", "upgrade", "code-context-engine"]
    elif "/pipx/" in cce_str:
        installer = "pipx"
        upgrade_cmd = ["pipx", "upgrade", "code-context-engine"]
    else:
        # Check if inside a uv tool environment by looking at the venv path
        venv_path = str(Path(sys.prefix).resolve())
        if "uv/tools" in venv_path:
            installer = "uv"
            upgrade_cmd = ["uv", "tool", "upgrade", "code-context-engine"]
        elif "pipx/venvs" in venv_path:
            installer = "pipx"
            upgrade_cmd = ["pipx", "upgrade", "code-context-engine"]
        else:
            installer = "pip"
            upgrade_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "code-context-engine"]

    lines.append(f"    Install method:  {click.style(installer, fg='cyan')}")

    if check:
        lines.append("")
        lines.append(f"    To upgrade: {click.style(' '.join(upgrade_cmd), fg='cyan')}")
        lines.append("")
        animate(lines)
        return

    lines.append(f"    Running:         {_dim(' '.join(upgrade_cmd))}")
    animate(lines)
    click.echo("")

    result = subprocess.run(upgrade_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        click.echo(f"  {CROSS} {click.style('Upgrade failed', fg='red')}")
        if result.stderr:
            for err_line in result.stderr.strip().splitlines()[-5:]:
                click.echo(f"    {_dim(err_line)}")
        click.echo("")
        sys.exit(1)

    # Show what pip/uv printed (version info)
    output = (result.stdout or result.stderr or "").strip()
    for out_line in output.splitlines()[-3:]:
        click.echo(f"    {_dim(out_line)}")

    new_version = current  # fallback
    try:
        # The running process still sees the old venv metadata, so shell out
        # to the upgraded executable to get the real post-upgrade version.
        ver_result = subprocess.run(
            [str(cce_bin), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if ver_result.returncode == 0:
            # Output format: "cce, version X.Y.Z"
            new_version = ver_result.stdout.strip().rsplit(None, 1)[-1]
    except Exception:
        pass

    click.echo("")
    if new_version != current:
        _ok(f"Upgraded {click.style(current, fg='white')} → {click.style(new_version, fg='green', bold=True)}")
    else:
        _ok(f"Already on latest version ({click.style(current, fg='cyan')})")

    # Refresh project config if in an initialized project
    project_dir = _safe_cwd()
    mcp_path = project_dir / ".mcp.json"
    if mcp_path.exists():
        click.echo("")
        click.echo(f"  {click.style('Refreshing project config', fg='cyan')}...")
        configured = _configure_mcp(project_dir)
        if configured:
            _ok("MCP server paths updated in " + click.style(".mcp.json", fg="cyan"))
        else:
            _ok("MCP server config is current")
        _ensure_claude_md(project_dir)
        _ensure_session_hook(project_dir)
        from context_engine.indexer.git_hooks import install_hooks
        if (project_dir / ".git").exists():
            install_hooks(str(project_dir))
            _ok("Git hooks refreshed")

    click.echo("")
    click.echo(
        click.style("  Done!", fg="green", bold=True) +
        click.style("  Restart your AI coding agent to pick up changes.", fg="white")
    )
    click.echo("")


def savings_shortcut() -> None:
    """Entry point for the `cce-savings` shortcut command."""
    @click.command()
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    @click.option("--all", "all_projects", is_flag=True, help="Show all projects")
    def _cmd(as_json: bool, all_projects: bool) -> None:
        """Show CCE token savings — how much context compression is saving you."""
        project_path = _safe_cwd() / PROJECT_CONFIG_NAME
        config = load_config(project_path=project_path if project_path.exists() else None)
        _run_savings_report(config, as_json=as_json, all_projects=all_projects)

    _cmd()


def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@main.command()
@click.option("--http", "as_http", is_flag=True, help="Start HTTP REST server instead of stdio MCP")
@click.option("--host", default="127.0.0.1", show_default=True, help="HTTP bind host (requires CCE_API_TOKEN for non-loopback)")
@click.option("--port", default=8765, show_default=True, help="HTTP port")
@click.option("--project-dir", default=None, help="Project directory (defaults to cwd)")
@click.pass_context
def serve(ctx: click.Context, as_http: bool, host: str, port: int, project_dir: str | None) -> None:
    """Start the MCP server (used by Claude Code).

    With --http, starts a REST server exposing the storage backend for remote
    backend clients. Binds loopback by default; exposing on other interfaces
    requires CCE_API_TOKEN to be set.
    """
    if project_dir:
        import os
        os.chdir(project_dir)
        # Click's main() loaded config from the launch cwd; if the user pointed
        # us at a different project, re-load so its .context-engine.yaml wins.
        # Without this, launchers running `cce serve --project-dir /repo` from
        # a different cwd would silently ignore /repo/.context-engine.yaml.
        target_config = Path(project_dir) / PROJECT_CONFIG_NAME
        ctx.obj["config"] = load_config(
            project_path=target_config if target_config.exists() else None
        )
    if as_http:
        from context_engine.serve_http import run_http_server
        run_http_server(ctx.obj["config"], host=host, port=port)
        return
    from importlib.metadata import version as pkg_version
    try:
        ver = pkg_version("code-context-engine")
    except Exception:
        ver = "unknown"
    click.echo(f"CCE v{ver} · Starting context engine MCP server...", err=True)
    asyncio.run(_run_serve(ctx.obj["config"]))


@main.command()
@click.option("--port", default=0, type=int, help="Port to listen on (0 = random free port)")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
@click.pass_context
def dashboard(ctx: click.Context, port: int, no_browser: bool) -> None:
    """Start the web dashboard for index inspection."""
    import os as _os
    import webbrowser
    import uvicorn
    from context_engine.dashboard.server import create_app

    config = ctx.obj["config"]
    project_dir = _safe_cwd()

    if port == 0:
        port = _find_free_port()

    # When CCE_DASHBOARD_TOKEN is set, append it to the URL so the dashboard
    # JS picks it up and includes it on mutating requests. Without the token
    # in the URL the page itself loads fine but Reindex / Clear / etc. would
    # 401 — most users would assume the dashboard was broken, so we surface
    # the URL with the token already attached.
    token = (_os.environ.get("CCE_DASHBOARD_TOKEN") or "").strip()
    from context_engine.cli_style import dim
    base_url = f"http://localhost:{port}"
    url = f"{base_url}?token={token}" if token else base_url
    click.echo(f"  {header('CCE Dashboard')} at {value(url)}")
    if token:
        click.echo(f"  {dim('Auth: bearer token required for write actions.')}")
    click.echo(f"  {dim('Press Ctrl+C to stop.')}")

    if not no_browser:
        webbrowser.open(url)

    app = create_app(config, project_dir)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


# ── services command group ────────────────────────────────────────────────────

@main.group(invoke_without_command=True)
@click.pass_context
def services(ctx: click.Context) -> None:
    """Show status of CCE services (Ollama, Dashboard)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(services_status)


@services.command(name="status")
def services_status() -> None:
    """Show status of all CCE services."""
    from context_engine.services import get_ollama_status, get_dashboard_status, get_mcp_status
    from context_engine.cli_style import dim, section, animate, warn, BULLET, BULLET_OFF

    rows = [
        get_ollama_status(),
        get_dashboard_status(),
        get_mcp_status(),
    ]

    lines: list[str] = []
    lines.append("")
    lines.append(section("Services"))
    lines.append("")

    for row in rows:
        running = row["running"]
        bullet = BULLET if running else BULLET_OFF
        status_text = success("running") if running else warn("stopped")
        detail = dim(row.get("detail", ""))
        name = value(f"{row['name']:<12}")
        lines.append(f"    {bullet} {name} {status_text}  {detail}")

    lines.append("")
    animate(lines)


@services.command(name="start")
@click.argument("service", required=False, type=click.Choice(["ollama", "dashboard", "all"]), default="all")
@click.option("--port", default=8080, show_default=True, help="Dashboard port (only used when starting dashboard)")
def services_start(service: str, port: int) -> None:
    """Start CCE services. SERVICE: ollama | dashboard | all (default)."""
    from context_engine.services import start_ollama, start_dashboard

    targets = ["ollama", "dashboard"] if service == "all" else [service]

    for target in targets:
        if target == "ollama":
            ok, msg = start_ollama()
        else:
            ok, msg = start_dashboard(port=port)
        prefix = click.style("✓", fg="green") if ok else click.style("·", fg="yellow")
        click.echo(f"  {prefix} {msg}")


@services.command(name="stop")
@click.argument("service", required=False, type=click.Choice(["ollama", "dashboard", "all"]), default="all")
def services_stop(service: str) -> None:
    """Stop CCE services. SERVICE: ollama | dashboard | all (default)."""
    from context_engine.services import stop_ollama, stop_dashboard

    targets = ["ollama", "dashboard"] if service == "all" else [service]

    for target in targets:
        if target == "ollama":
            ok, msg = stop_ollama()
        else:
            ok, msg = stop_dashboard()
        prefix = click.style("✓", fg="green") if ok else click.style("·", fg="yellow")
        click.echo(f"  {prefix} {msg}")


# ── sessions command group ────────────────────────────────────────────────────

@main.group(invoke_without_command=True)
@click.pass_context
def sessions(ctx: click.Context) -> None:
    """Inspect and prune cross-session memory."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@sessions.command(name="status")
@click.pass_context
def sessions_status(ctx: click.Context) -> None:
    """Show health of this project's memory: db size, counts, queue, schema.

    Works without `cce serve` — it just opens memory.db read-only and runs
    a few queries. Useful for "is memory actually capturing anything?"
    """
    from context_engine.memory import db as memory_db

    config = ctx.obj["config"]
    project_name = _safe_cwd().name
    storage_base = project_storage_dir(config, _safe_cwd())
    db_path = memory_db.memory_db_path(storage_base)

    click.echo(f"  project: {project_name}")
    click.echo(f"  storage: {storage_base}")
    if not db_path.exists():
        click.echo(
            f"  {DOT} memory.db not initialised ({db_path}). Run "
            f"`cce serve` for one prompt — the schema bootstraps on first open."
        )
        return

    size_bytes = db_path.stat().st_size
    click.echo(f"  memory.db: {db_path}  ({size_bytes // 1024} KB)")
    conn = memory_db.connect(db_path)
    try:
        version = memory_db.schema_version(conn)
        has_vec = memory_db.has_vec_tables(conn)
        click.echo(
            f"  schema:    v{version}"
            f"{' · sqlite-vec available' if has_vec else ' · vec disabled'}"
        )

        # Sessions counts.
        sess_rows = list(conn.execute(
            "SELECT status, COUNT(*) AS n FROM sessions GROUP BY status"
        ))
        if sess_rows:
            parts = ", ".join(f"{r['status']}={r['n']}" for r in sess_rows)
            click.echo(f"  sessions:  {parts}")
        else:
            click.echo("  sessions:  none recorded yet")

        # Decisions by source — biggest signal of "is capture working?"
        dec_rows = list(conn.execute(
            "SELECT source, COUNT(*) AS n FROM decisions GROUP BY source"
        ))
        if dec_rows:
            parts = ", ".join(f"{r['source']}={r['n']}" for r in dec_rows)
            click.echo(f"  decisions: {parts}")
        else:
            click.echo(f"  decisions: 0  ({DOT} no record_decision calls yet)")

        # Turn summaries (compress worker output).
        turn_count = conn.execute(
            "SELECT COUNT(*) AS n FROM turn_summaries"
        ).fetchone()["n"]
        click.echo(f"  turns:     {turn_count} compressed summaries")

        # Compression queue depth — a stuck worker shows up here.
        queue = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(attempts), 0) AS max_att "
            "FROM pending_compressions"
        ).fetchone()
        if queue["n"]:
            attn = (
                f"  ({CROSS} max attempts={queue['max_att']})"
                if queue["max_att"] > 1 else ""
            )
            click.echo(f"  queue:     {queue['n']} pending{attn}")
        else:
            click.echo("  queue:     drained")

        # Vec coverage — backfill check.
        if has_vec:
            dec_v = conn.execute(
                "SELECT COUNT(*) AS n FROM decisions_vec"
            ).fetchone()["n"]
            turn_v = conn.execute(
                "SELECT COUNT(*) AS n FROM turn_summaries_vec"
            ).fetchone()["n"]
            dec_total = sum(r["n"] for r in dec_rows) if dec_rows else 0
            click.echo(
                f"  vec:       decisions={dec_v}/{dec_total}, "
                f"turns={turn_v}/{turn_count}"
            )

        # Raw payload retention — how much of memory.db is unbounded data.
        payloads = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS total "
            "FROM tool_event_payloads WHERE raw_input != ''"
        ).fetchone()
        if payloads["n"]:
            mb = payloads["total"] // (1024 * 1024)
            click.echo(
                f"  payloads:  {payloads['n']} retained "
                f"(~{mb} MB raw; `cce sessions prune` ages out >30d)"
            )
        else:
            click.echo("  payloads:  none retained")
    finally:
        conn.close()


@sessions.command(name="prune")
@click.option(
    "--threshold",
    default=100,
    show_default=True,
    type=int,
    help="Consolidate when more than this many session files exist",
)
@click.option(
    "--keep",
    default=50,
    show_default=True,
    type=int,
    help="Number of most-recent sessions to keep verbatim",
)
@click.option(
    "--retain-payloads-days",
    default=30,
    show_default=True,
    type=int,
    help=(
        "Drop raw tool inputs/outputs older than this many days from memory.db. "
        "Summaries are kept; only the unbounded raw payload bytes are nulled."
    ),
)
@click.pass_context
def sessions_prune(
    ctx: click.Context, threshold: int, keep: int, retain_payloads_days: int,
) -> None:
    """Consolidate old session files and age out raw memory.db payloads.

    Two independent jobs:
      1. JSON sessions → decisions_log.json (`SessionCapture.prune_old_sessions`)
      2. memory.db `tool_event_payloads.raw_input/raw_output` older than
         --retain-payloads-days are NULLed. Summaries (turn_summaries,
         decisions, code_areas) are untouched and stay searchable.
    """
    from context_engine.integration.session_capture import SessionCapture
    from context_engine.memory import db as memory_db

    config = ctx.obj["config"]
    storage_base = project_storage_dir(config, _safe_cwd())
    sessions_dir = storage_base / "sessions"

    if sessions_dir.exists():
        capture = SessionCapture(sessions_dir=str(sessions_dir))
        summary = capture.prune_old_sessions(threshold=threshold, keep=keep)
        pruned = summary.get("pruned", 0)
        appended = summary.get("decisions_appended", 0)
        if pruned == 0:
            reason = summary.get("reason", "")
            click.echo(f"  {DOT} JSON sessions: nothing to prune ({reason}).")
        else:
            click.echo(
                f"  {CHECK} JSON sessions: pruned {pruned} file(s); "
                f"archived {appended} decision(s) to decisions_log.json."
            )
    else:
        click.echo(f"  {DOT} JSON sessions: no directory at {sessions_dir}")

    db_path = memory_db.memory_db_path(storage_base)
    if not db_path.exists():
        click.echo(f"  {DOT} memory.db: not initialised at {db_path}")
        return
    conn = memory_db.connect(db_path)
    try:
        out = memory_db.prune_old_payloads(conn, days=retain_payloads_days)
    finally:
        conn.close()
    n = out["payloads_pruned"]
    if n == 0:
        click.echo(
            f"  {DOT} memory.db: no raw payloads older than "
            f"{retain_payloads_days}d to prune."
        )
    else:
        kb = out["bytes_freed_estimate"] // 1024
        click.echo(
            f"  {CHECK} memory.db: aged out {n} raw payload(s) "
            f"(~{kb} KB freed; summaries retained)."
        )


@sessions.command(name="export")
@click.option(
    "--since", "since_iso", type=str, default=None,
    help="Only include rows created on/after this date (YYYY-MM-DD or ISO-8601).",
)
@click.option(
    "--until", "until_iso", type=str, default=None,
    help="Only include rows created before this date.",
)
@click.option(
    "--format", "fmt", type=click.Choice(["markdown", "json"]),
    default="markdown", help="Output format.",
)
@click.option(
    "--output", "-o", type=click.Path(dir_okay=False), default=None,
    help="Write to this file instead of stdout.",
)
@click.pass_context
def sessions_export(
    ctx: click.Context,
    since_iso: str | None,
    until_iso: str | None,
    fmt: str,
    output: str | None,
) -> None:
    """Export decisions + turn summaries from this project's memory.db.

    Useful for: quarterly reviews, hand-off docs, post-mortem digests,
    grepping a long-running project's history outside the index. Default
    is markdown to stdout; pass `--format json` for machine-readable.

    Examples:
        cce sessions export --since 2026-01-01 -o q1-decisions.md
        cce sessions export --since 2026-04-01 --until 2026-04-30 --format json
    """
    import datetime
    import json as _json
    from context_engine.memory import db as memory_db

    config = ctx.obj["config"]
    project_name = _safe_cwd().name
    storage_base = project_storage_dir(config, _safe_cwd())
    db_path = memory_db.memory_db_path(storage_base)
    if not db_path.exists():
        click.echo("  No memory.db for this project — nothing to export.")
        return

    def _parse(s: str | None) -> int | None:
        if not s:
            return None
        try:
            # Accept date-only or ISO-8601.
            if "T" in s:
                dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = datetime.datetime.fromisoformat(s)
            return int(dt.timestamp())
        except ValueError:
            raise click.BadParameter(
                f"could not parse {s!r} as a date — use YYYY-MM-DD or ISO-8601"
            )

    since_epoch = _parse(since_iso)
    until_epoch = _parse(until_iso)

    conn = memory_db.connect(db_path)
    try:
        where, params = [], []
        if since_epoch is not None:
            where.append("created_at_epoch >= ?")
            params.append(since_epoch)
        if until_epoch is not None:
            where.append("created_at_epoch < ?")
            params.append(until_epoch)
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        decisions = list(conn.execute(
            f"SELECT decision, reason, created_at, source FROM decisions"
            f"{clause} ORDER BY created_at_epoch ASC",
            params,
        ))
        turns = list(conn.execute(
            f"SELECT session_id, prompt_number, summary, tier, created_at_epoch "
            f"FROM turn_summaries{clause} ORDER BY created_at_epoch ASC",
            params,
        ))
    finally:
        conn.close()

    if fmt == "json":
        payload = {
            "project": project_name,
            "since": since_iso,
            "until": until_iso,
            "decisions": [dict(r) for r in decisions],
            "turn_summaries": [dict(r) for r in turns],
        }
        text = _json.dumps(payload, indent=2, default=str)
    else:
        # Markdown — readable digest. Storage form is grammar-compressed;
        # `_grammar_expand` reverses abbreviations on the way out so the
        # exported text reads naturally without needing CCE installed.
        from context_engine.memory.grammar import expand as _expand
        lines = [f"# {project_name} — session export\n"]
        if since_iso or until_iso:
            lines.append(
                f"_Window: {since_iso or '(beginning)'} → "
                f"{until_iso or '(now)'}_\n"
            )
        lines.append(f"## Decisions ({len(decisions)})\n")
        for d in decisions:
            lines.append(f"### {_expand(d['decision'])}")
            lines.append(f"_{d['created_at']} · source={d['source']}_\n")
            if d["reason"]:
                lines.append(f"{_expand(d['reason'])}\n")
        lines.append(f"\n## Turn Summaries ({len(turns)})\n")
        for t in turns:
            iso = datetime.datetime.fromtimestamp(
                t["created_at_epoch"], tz=datetime.UTC,
            ).isoformat(timespec="seconds")
            lines.append(
                f"- **{iso}** · session `{t['session_id'][:8]}` · "
                f"turn {t['prompt_number']} · {t['tier']}: "
                f"{_expand(t['summary'])}"
            )
        text = "\n".join(lines) + "\n"

    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"  ✓ Wrote {len(decisions)} decision(s) + "
                   f"{len(turns)} turn(s) to {output}")
    else:
        click.echo(text)


@sessions.command(name="migrate")
@click.option(
    "--no-archive",
    is_flag=True,
    default=False,
    help="Don't archive consumed JSON files into migrated.zip after import.",
)
@click.pass_context
def sessions_migrate(ctx: click.Context, no_archive: bool) -> None:
    """Import legacy per-session JSON files into the per-project memory.db.

    Idempotent: rerun is a no-op once everything has been imported. Imported
    decisions and code areas are tagged with source='migrated' so future
    session_recall can rank them appropriately.
    """
    from context_engine.memory import db as memory_db, migrate as memory_migrate

    config = ctx.obj["config"]
    project_name = _safe_cwd().name
    storage_base = project_storage_dir(config, _safe_cwd())
    db_path = memory_db.memory_db_path(storage_base)

    conn = memory_db.connect(db_path)
    try:
        summary = memory_migrate.migrate(
            conn,
            project_name=project_name,
            storage_base=storage_base,
            archive=not no_archive,
        )
    finally:
        conn.close()

    if not summary.sources_scanned:
        click.echo(f"  {DOT} No legacy session directories found.")
        return

    click.echo(f"  {CHECK} Scanned: {len(summary.sources_scanned)} source dir(s)")
    if summary.files_imported == 0 and summary.files_skipped > 0:
        click.echo(f"  {DOT} {summary.files_skipped} file(s) already imported. Nothing to do.")
        return
    click.echo(
        f"  {CHECK} Imported {summary.files_imported} file(s) → "
        f"{summary.decisions_imported} decision(s), "
        f"{summary.code_areas_imported} code area(s)."
    )
    if summary.files_archived:
        click.echo(f"  {CHECK} Archived {summary.files_archived} file(s) to migrated.zip")


async def _run_index(
    config,
    project_dir: str,
    full: bool = False,
    target_path: str | None = None,
    verbose: bool = False,
) -> None:
    """Run indexing pipeline (thin wrapper over `indexer.pipeline.run_indexing`)."""
    from context_engine.indexer.pipeline import run_indexing

    log_fn = (lambda msg: click.echo(msg)) if verbose else None
    from context_engine.cli_style import warn, dim, CHECK, CROSS

    _showed_progress = False
    _showed_embed_progress = False
    _bar_width = 30

    def _render_bar(current: int, total: int, label: str) -> None:
        filled = int(_bar_width * current / total) if total else 0
        bar = (
            click.style("█" * filled, fg="cyan") +
            click.style("░" * (_bar_width - filled), fg="bright_black")
        )
        pct = click.style(f"{int(100 * current / total) if total else 0}%", fg="bright_black")
        count = click.style(f"{current}/{total}", fg="white", bold=True)
        click.echo(f"\r    {bar}  {count} {label}  {pct}", nl=False)

    def progress_fn(current: int, total: int) -> None:
        nonlocal _showed_progress
        if not verbose and sys.stdout.isatty():
            _render_bar(current, total, "files")
            _showed_progress = True

    def embed_progress_fn(current: int, total: int) -> None:
        nonlocal _showed_embed_progress, _showed_progress
        if not verbose and sys.stdout.isatty():
            # First tick: close out the file bar (if any) with a newline so the
            # embed bar starts on its own line instead of overwriting it.
            if not _showed_embed_progress and _showed_progress:
                click.echo()
            _render_bar(current, total, "chunks embedded")
            _showed_embed_progress = True

    def phase_fn(msg: str) -> None:
        """Print a status line between indexing phases.

        Closes any in-place progress bar (chunking or embedding) on its own
        line first, so subsequent phase messages don't overwrite or mash
        into the bar. Complementary to embed_progress_fn — phase_fn is
        per-phase ("starting embedding"), embed_progress_fn is per-batch
        ("embedded 1024/32000 chunks"). Both keep large-repo indexing from
        looking like a hang.
        """
        nonlocal _showed_progress, _showed_embed_progress
        if _showed_progress or _showed_embed_progress:
            click.echo()  # finalise the in-place bar line
            _showed_progress = False
            _showed_embed_progress = False
        click.echo(f"    {_dim(msg)}")

    result = await run_indexing(
        config, project_dir, full=full, target_path=target_path,
        log_fn=log_fn, progress_fn=progress_fn,
        embed_progress_fn=embed_progress_fn, phase_fn=phase_fn,
    )

    if _showed_progress or _showed_embed_progress:
        click.echo()  # newline after progress bar(s)

    for err in result.errors:
        click.echo(f"  {CROSS} {warn(f'Error: {err}')}", err=True)

    n_files = len(result.indexed_files)
    detail_parts = []
    if result.deleted_files:
        detail_parts.append(f", pruned {warn(str(len(result.deleted_files)))} deleted")
    if result.skipped_files:
        detail_parts.append(f", skipped {dim(str(len(result.skipped_files)))} non-text")
    # Surface embedding-cache reuse so users see the speedup directly.
    if result.cache_hits > 0:
        total_embeds = result.cache_hits + result.cache_misses
        pct = int(result.cache_hits / total_embeds * 100)
        detail_parts.append(f", {dim(f'{pct}% cache hit')}")

    click.echo(
        f"  {CHECK} " +
        value(f"Indexed {result.total_chunks:,} chunks") +
        click.style(f" from {n_files:,} file{'s' if n_files != 1 else ''}", fg="white") +
        "".join(detail_parts)
    )

    # Update full_file_tokens baseline so cce savings shows codebase size
    _storage_dir = project_storage_dir(config, Path(project_dir))
    stats_path = _storage_dir / "stats.json"
    try:
        stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        stats = {}
    total_tokens = 0
    project_root = Path(project_dir)
    from context_engine.storage.local_backend import LocalBackend
    backend = LocalBackend(base_path=str(_storage_dir))
    for rel_path in backend._vector_store.file_chunk_counts():
        fp = project_root / rel_path
        if fp.exists():
            try:
                total_tokens += len(fp.read_text(errors="ignore")) // 4
            except OSError:
                pass
    stats["full_file_tokens"] = total_tokens
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats))


async def _run_serve(config) -> None:
    """Start MCP server with live file watcher."""
    import logging
    import signal
    # Force single-process embedding inside `cce serve` unless the user
    # explicitly overrode it. The reindex worker triggered by file changes
    # otherwise spawns a fastembed forkserver pool (~4 workers × ~1.6 GB on
    # Linux) that orphans on abnormal exit and leaks RSS across `cce index`
    # invocations (issue #66). Single-process embed is plenty for one-file
    # watcher reindexes; bulk `cce index` run from a separate shell still
    # gets the multiprocess path.
    os.environ.setdefault("CCE_EMBED_PARALLEL", "0")

    from context_engine.storage.local_backend import LocalBackend
    from context_engine.indexer.embedder import Embedder
    from context_engine.retrieval.retriever import HybridRetriever
    from context_engine.compression.compressor import Compressor
    from context_engine.integration.mcp_server import ContextEngineMCP
    from context_engine.indexer.watcher import FileWatcher
    from context_engine.indexer.pipeline import run_indexing

    _log = logging.getLogger("context_engine.watcher")

    project_dir = str(_safe_cwd())
    project_name = _safe_cwd().name
    storage_base = project_storage_dir(config, _safe_cwd())
    backend = LocalBackend(base_path=str(storage_base))
    embedder = Embedder(model_name=config.embedding_model)
    retriever = HybridRetriever(backend=backend, embedder=embedder)
    compressor = Compressor(
        model=config.compression_model,
        ollama_url=resolve_ollama_url(config),
        cache=backend,
    )
    mcp = ContextEngineMCP(
        retriever=retriever, backend=backend, compressor=compressor,
        embedder=embedder, config=config,
    )

    chunk_count = backend._vector_store.count()
    import sys

    watcher = None
    worker_task = None

    if config.indexer_watch:
        # Live file watcher — re-indexes changed files on save.
        _reindex_queue: asyncio.Queue[str] = asyncio.Queue()
        _reindex_pending: set[str] = set()

        async def _on_file_change(file_path: str):
            """Queue the file for re-indexing, deduplicating pending entries."""
            try:
                rel = str(Path(file_path).relative_to(project_dir))
            except ValueError:
                return
            if rel not in _reindex_pending:
                _reindex_pending.add(rel)
                await _reindex_queue.put(rel)

        async def _reindex_worker():
            """Background task that processes re-index requests sequentially."""
            while True:
                rel = await _reindex_queue.get()
                _reindex_pending.discard(rel)
                try:
                    await run_indexing(config, project_dir, target_path=rel)
                    _log.debug("Re-indexed: %s", rel)
                except Exception as exc:
                    _log.warning("Watch re-index failed for %s: %s", rel, exc)
                _reindex_queue.task_done()

        watcher = FileWatcher(
            watch_dir=project_dir,
            on_change=_on_file_change,
            debounce_ms=config.indexer_debounce_ms,
            ignore_patterns=config.indexer_ignore,
        )

        loop = asyncio.get_running_loop()
        worker_task = asyncio.create_task(_reindex_worker())
        watcher.start(loop=loop)

    # Memory hook listener — loopback HTTP for the 5 lifecycle hooks. Best
    # effort: a setup failure here must NOT prevent the MCP server starting
    # (capture is a non-critical feature; retrieval still works without it).
    hook_runner = None
    hook_port = None
    try:
        from context_engine.memory.hook_server import start_hook_server
        hook_runner, hook_port = await start_hook_server(
            storage_base=storage_base, project_name=project_name,
        )
    except Exception as exc:
        _log.warning("Memory hook server failed to start: %s", exc)

    # Memory compression worker — drains pending_compressions in the background.
    # Each iteration opens a thread-local SQLite connection inside
    # `asyncio.to_thread`, so this loop never holds the asyncio thread while
    # an embed + SQLite write is in flight.
    compression_task = None
    auto_prune_task = None
    try:
        from context_engine.memory import db as memory_db
        from context_engine.memory.compressor import compression_loop
        compression_task = asyncio.create_task(
            compression_loop(memory_db.memory_db_path(storage_base), embedder)
        )
    except Exception as exc:
        _log.warning("Memory compression worker failed to start: %s", exc)

    # Auto-prune — run prune_old_payloads in the background so users who
    # never invoke `cce sessions prune` manually still get bounded memory.db
    # growth.
    try:
        from context_engine.memory.db import auto_prune_loop
        auto_prune_task = asyncio.create_task(
            auto_prune_loop(storage_base, days=30)
        )
    except Exception as exc:
        _log.warning("Auto-prune worker failed to start: %s", exc)

    watcher_label = " · live watcher active" if watcher else ""
    hook_label = f" · memory hooks :{hook_port}" if hook_port else ""
    print(
        f"CCE ready · {project_name} · {chunk_count} chunks indexed"
        f"{watcher_label}{hook_label}",
        file=sys.stderr,
    )

    # Install signal handlers so SIGINT (Ctrl-C), SIGTERM, and SIGHUP all
    # route through the same orderly shutdown path. Previously only SIGTERM
    # cancelled the MCP task — SIGINT was swallowed by stdio reads, leaving
    # `cce serve` unkillable except via SIGKILL, which orphans the embed
    # workers (#66).
    serve_loop = asyncio.get_running_loop()
    mcp_task = asyncio.create_task(mcp.run_stdio())

    def _request_shutdown(signame: str) -> None:
        if not mcp_task.done():
            _log.info("Received %s, shutting down...", signame)
            mcp_task.cancel()

    # Build the candidate list with getattr so we don't reference
    # `signal.SIGHUP` at the tuple-construction site — SIGHUP is
    # undefined on Windows and that AttributeError would fire *before*
    # the try/except below could swallow it, crashing `cce serve` on
    # Windows entirely (Copilot review on #69).
    installed_signals: list[int] = []
    candidate_sigs = [
        s for s in (
            getattr(signal, "SIGINT", None),
            getattr(signal, "SIGTERM", None),
            getattr(signal, "SIGHUP", None),
        ) if s is not None
    ]
    for _sig in candidate_sigs:
        try:
            serve_loop.add_signal_handler(
                _sig, _request_shutdown, _sig.name,
            )
            installed_signals.append(_sig)
        except (NotImplementedError, RuntimeError):
            # Windows's ProactorEventLoop refuses add_signal_handler;
            # asyncio also raises NotImplementedError outside the main
            # thread. SIGTERM still arrives via the default Python
            # handler in those environments.
            pass

    try:
        try:
            await mcp_task
        except asyncio.CancelledError:
            pass
    finally:
        for _sig in installed_signals:
            try:
                serve_loop.remove_signal_handler(_sig)
            except (NotImplementedError, RuntimeError):
                pass
        if watcher:
            watcher.stop()
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        if compression_task is not None:
            compression_task.cancel()
            try:
                await compression_task
            except asyncio.CancelledError:
                pass
        if auto_prune_task is not None:
            auto_prune_task.cancel()
            try:
                await auto_prune_task
            except asyncio.CancelledError:
                pass
        if hook_runner is not None:
            try:
                await hook_runner.cleanup()
            except Exception:
                _log.warning("hook_runner cleanup failed", exc_info=True)
