# src/context_engine/cli.py
"""CLI entry point for code-context-engine."""
import asyncio
import json
import socket
import sys
from pathlib import Path

import click

from context_engine.config import load_config, PROJECT_CONFIG_NAME


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
_CCE_CLAUDE_MD_VERSION = "2"
_CCE_CLAUDE_MD_VERSION_TAG = f"<!-- cce-block-version: {_CCE_CLAUDE_MD_VERSION} -->"
_CCE_CLAUDE_MD_VERSION_PREFIX = "<!-- cce-block-version: "
_CCE_CLAUDE_MD_END_MARKER = "<!-- /cce-block -->"

_CCE_CLAUDE_MD_BLOCK = f"""\
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

## Output Style

Be concise. Lead with the answer or action, not reasoning. Skip filler words,
preamble, and phrases like "I'll help you with that" or "Certainly!". Prefer
fragments over full sentences in explanations. No trailing summaries of what
you just did. One sentence if it fits.

Code blocks, file paths, commands, and error messages are always written in full.
{_CCE_CLAUDE_MD_END_MARKER}
"""


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

    project_dir = Path.cwd()
    project_name = project_dir.name
    storage_dir = Path(config.storage_path) / project_name

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
            left_lines.append(f" {D}○ Savings      no queries yet{R}")
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

    Downloads the embedding model on first use with a clear progress message,
    and reports Ollama status so users know what compression level they will get.
    """
    # --- Embedding model ---
    click.echo(_dim("  Checking embedding model") + "...", nl=False)
    try:
        from fastembed import TextEmbedding
        model_name = getattr(config, "embedding_model", "BAAI/bge-small-en-v1.5")
        if "/" not in model_name:
            model_name = f"sentence-transformers/{model_name}"
        click.echo(_dim(" downloading if needed (60 MB, first time only)") + "...", nl=False)
        TextEmbedding(model_name)
        click.echo(" " + click.style("ready", fg="green"))
    except Exception as exc:
        click.echo("")
        _warn(f"Could not load embedding model: {exc}")
        _warn("Indexing will attempt to continue but may fail.")

    # --- Ollama (optional) ---
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


def _ensure_claude_md(project_dir: Path) -> None:
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

    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.exists():
        atomic_write_text(claude_md, _CCE_CLAUDE_MD_BLOCK)
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
        new_content = existing.replace(old_block, _CCE_CLAUDE_MD_BLOCK.rstrip(), 1)
        atomic_write_text(claude_md, new_content)
        _ok("CLAUDE.md upgraded to current CCE instructions")
        return

    # No CCE block detected — append.
    new_content = existing.rstrip() + "\n\n" + _CCE_CLAUDE_MD_BLOCK
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
    project_path = Path.cwd() / PROJECT_CONFIG_NAME
    ctx.obj["config"] = load_config(project_path=project_path if project_path.exists() else None)
    ctx.obj["verbose"] = verbose

    if ctx.invoked_subcommand is None:
        _show_welcome_banner(ctx.obj["config"])


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize context engine and connect it to Claude Code."""
    from context_engine.indexer.git_hooks import install_hooks
    from context_engine.project_commands import ensure_gitignore
    config = ctx.obj["config"]
    project_dir = Path.cwd()

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
    project_name = project_dir.name
    storage_dir = Path(config.storage_path) / project_name
    storage_dir.mkdir(parents=True, exist_ok=True)
    meta_path = storage_dir / "meta.json"
    meta_path.write_text(json.dumps({"project_dir": str(project_dir.resolve())}))

    # 3. Git hooks
    is_git_repo = (project_dir / ".git").exists()
    if is_git_repo:
        installed = install_hooks(str(project_dir))
        if installed:
            _ok(f"Git hooks installed  " + _dim(f"({len(installed)} hooks, auto-updates on commit)"))
    else:
        _warn("Not a git repository — git hook skipped")
        click.echo(_dim("    Run `cce index` manually after making changes."))

    # 4. MCP config
    configured = _configure_mcp(project_dir)
    if configured:
        _ok("MCP server registered in " + click.style(".mcp.json", fg="cyan"))
    else:
        _ok("MCP server already configured in " + click.style(".mcp.json", fg="cyan"))

    # 5. CLAUDE.md + session hook
    _ensure_claude_md(project_dir)
    _ensure_session_hook(project_dir)

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
        click.style("  Restart Claude Code to activate CCE.", fg="white")
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
    project_dir = str(Path.cwd())
    from context_engine.cli_style import section, animate
    lines = ["", section("Indexing " + Path.cwd().name)]
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
        project_name = Path.cwd().name
        storage = Path(config.storage_path) / project_name
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
        header, label, value, dim, success, warn, magenta, section, animate,
        CHECK, DOT, CROSS, BULLET, BULLET_OFF,
    )

    lines: list[str] = []
    lines.append("")
    lines.append(section("Status · " + Path.cwd().name))
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
    project_name = Path.cwd().name
    stats_path = Path(config.storage_path) / project_name / "stats.json"
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
            saved = max(0, baseline - served)
            pct = int(saved / baseline * 100) if baseline > 0 else 0
            lines.append(f"    {dim('Queries:')}        {value(f'{queries:,}')}")
            lines.append(f"    {dim('Full codebase:')}  {value(f'{baseline:,}')} {dim('tokens')}")
            lines.append(f"    {dim('Served:')}         {value(f'{served:,}')} {dim('tokens')}")
            lines.append(f"    {CHECK} {success(f'Saved: {saved:,} tokens ({pct}%)')}")
        except (KeyError, _json.JSONDecodeError):
            lines.append(f"    {DOT} {dim('Error reading stats')}")
    else:
        storage_dir = Path(config.storage_path) / Path.cwd().name
        vectors_dir = storage_dir / "vectors"
        if not vectors_dir.exists():
            lines.append(f"    {DOT} {dim('Project not indexed yet')}  {label('cce init')}")
        else:
            lines.append(f"    {DOT} {dim('No usage recorded yet')}  {dim('run context_search via MCP')}")

    # Embedding cache stats — surfaces how much the cache is actually saving.
    cache_db = Path(config.storage_path) / Path.cwd().name / "embedding_cache.db"
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
    from context_engine.cli_style import header, dim, value, label, section, animate, ARROW

    groups = [
        ("Setup", [
            ("cce init", "Index project, install git hooks, write .mcp.json"),
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
    from context_engine.cli_style import success, warn, CHECK, DOT
    existing = load_project_only(str(Path.cwd())).get(hook, [])
    if command in existing:
        click.echo(f"  {DOT} {warn('Already exists')} in {hook}: {command}")
        return
    add_command(str(Path.cwd()), hook, command)
    click.echo(f"  {CHECK} {success('Added')} to {hook}: {command}")


@commands.command("add-custom")
@click.argument("name")
@click.argument("command")
def commands_add_custom(name: str, command: str) -> None:
    """Add a named custom command. Example: cce commands add-custom deploy 'kubectl apply -f k8s/'"""
    from context_engine.project_commands import add_custom_command
    from context_engine.cli_style import success, CHECK
    add_custom_command(str(Path.cwd()), name, command)
    click.echo(f"  {CHECK} {success('Added')} custom command '{name}': {command}")


@commands.command("remove")
@click.argument("hook")
@click.argument("command")
def commands_remove(hook: str, command: str) -> None:
    """Remove a command from a hook."""
    from context_engine.project_commands import remove_command
    from context_engine.cli_style import success, warn, CHECK, DOT
    if remove_command(str(Path.cwd()), hook, command):
        click.echo(f"  {CHECK} {success('Removed')} from {hook}: {command}")
    else:
        click.echo(f"  {DOT} {warn('Not found')} in {hook}: {command}")


@commands.command("add-rule")
@click.argument("rule")
def commands_add_rule(rule: str) -> None:
    """Add a project rule. Example: cce commands add-rule 'Never use down() in migrations'"""
    from context_engine.project_commands import load_project_only, add_rule
    existing = load_project_only(str(Path.cwd())).get("rules", [])
    from context_engine.cli_style import success, warn, CHECK, DOT
    if rule in existing:
        click.echo(f"  {DOT} {warn('Already exists')}: {rule}")
        return
    add_rule(str(Path.cwd()), rule)
    click.echo(f"  {CHECK} {success('Rule added')}: {rule}")


@commands.command("remove-rule")
@click.argument("rule")
def commands_remove_rule(rule: str) -> None:
    """Remove a project rule."""
    from context_engine.project_commands import remove_rule
    from context_engine.cli_style import success, warn, CHECK, DOT
    if remove_rule(str(Path.cwd()), rule):
        click.echo(f"  {CHECK} {success('Rule removed')}: {rule}")
    else:
        click.echo(f"  {DOT} {warn('Not found')}: {rule}")


@commands.command("set-pref")
@click.argument("key")
@click.argument("value")
def commands_set_pref(key: str, value: str) -> None:
    """Set a preference. Example: cce commands set-pref database PostgreSQL"""
    from context_engine.project_commands import set_preference
    from context_engine.cli_style import success, CHECK
    set_preference(str(Path.cwd()), key, value)
    click.echo(f"  {CHECK} {success('Preference set')}: {key} = {value}")


@commands.command("remove-pref")
@click.argument("key")
def commands_remove_pref(key: str) -> None:
    """Remove a preference."""
    from context_engine.project_commands import remove_preference
    from context_engine.cli_style import success, warn, CHECK, DOT
    if remove_preference(str(Path.cwd()), key):
        click.echo(f"  {CHECK} {success('Preference removed')}: {key}")
    else:
        click.echo(f"  {DOT} {warn('Not found')}: {key}")


@commands.command("list")
def commands_list() -> None:
    """Show all project commands, rules, and preferences (merged with workspace)."""
    from context_engine.project_commands import load_commands
    from context_engine.cli_style import header, label, dim, value, section, animate, DOT, ARROW, BULLET

    cmds = load_commands(str(Path.cwd()))
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

    from context_engine.cli_style import header, label, value, dim, success, bold

    _COLS = 10

    def _fmt_k(n: int) -> str:
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    _FILLED = "⛁"
    _EMPTY = "⛶"

    def _grid_rows(used_pct: float, rows: int) -> list[str]:
        total = _COLS * rows
        filled = max(0, min(total, round(used_pct * total)))
        result = []
        for r in range(rows):
            cells = []
            for c in range(_COLS):
                if r * _COLS + c < filled:
                    cells.append(click.style(_FILLED, fg="cyan"))
                else:
                    cells.append(click.style(_EMPTY, dim=True))
            result.append("     " + " ".join(cells))
        return result

    def _print_project(name: str, stats: dict) -> None:
        full_file = stats.get("full_file_tokens", 0)
        served = stats.get("served_tokens", 0)
        queries = stats.get("queries", 0)
        raw = stats.get("raw_tokens", 0)

        # Two distinct savings effects, reported separately so the headline
        # number isn't muddled by mixing "we didn't dump full files" with
        # "we summarised what we did serve":
        #   retrieval = (full_file - raw)  / full_file
        #   compression = (raw - served)   / raw
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
        retrieval_pct = max(0, retrieval_pct)
        compression_pct = max(0, compression_pct)

        baseline = max(full_file, raw) if full_file > 0 else raw
        used_pct = served / baseline if baseline > 0 else 0

        labels: list[str] = [
            f"  {bold(name)} {dim('·')} {value(f'{queries:,}')} {dim('queries')}",
            f"  {value(_fmt_k(served))} {dim('served')}  {dim('·')}  "
            f"{value(_fmt_k(raw))} {dim('chunks raw')}  {dim('·')}  "
            f"{value(_fmt_k(full_file))} {dim('full-file baseline')}",
            "",
            f"  {header('Token savings (split)')}",
            f"  {click.style(_FILLED, fg='cyan')} {label('Retrieval:  ')}"
            f"  {success(f'{retrieval_pct:>3}%')}  {dim('vs reading full files')}",
            f"  {click.style(_EMPTY, dim=True)} {label('Compression:')}"
            f"  {success(f'{compression_pct:>3}%')}  {dim('chunk → summary')}",
        ]

        grid = _grid_rows(used_pct, rows=len(labels))
        click.echo()
        for g, l in zip(grid, labels):
            click.echo(f"{g}   {l}")

    def _json_entry(name: str, stats: dict) -> dict:
        full_file = stats.get("full_file_tokens", 0)
        raw = stats.get("raw_tokens", 0)
        served = stats.get("served_tokens", 0)
        baseline = max(full_file, raw) if full_file > 0 else raw
        saved = baseline - served
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
            # Kept for backward compat with anything scraping this JSON:
            "savings_pct": int(saved / baseline * 100) if baseline > 0 else 0,
            "retrieval_savings_pct": max(0, retrieval_pct),
            "compression_savings_pct": max(0, compression_pct),
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
        project_name = Path.cwd().name
        project_dirs = [storage_root / project_name]

    reports: list[tuple[str, dict]] = []
    for pd in project_dirs:
        stats = _load_stats(pd)
        if stats is not None:
            reports.append((pd.name, stats))

    if not reports:
        if as_json:
            if all_projects:
                click.echo(_json.dumps({"projects": []}))
            else:
                click.echo(_json.dumps(_json_entry(Path.cwd().name, {
                    "raw_tokens": 0, "served_tokens": 0, "queries": 0,
                })))
        else:
            click.echo(f"  {dim('No usage recorded yet.')}")
            click.echo(f"  {dim('Run context_search queries via MCP to start tracking savings.')}")
        return

    if as_json:
        if all_projects:
            click.echo(_json.dumps(
                {"projects": [_json_entry(n, s) for n, s in reports]}, indent=2,
            ))
        else:
            click.echo(_json.dumps(_json_entry(*reports[0]), indent=2))
        return

    # Text output
    for name, stats in reports:
        _print_project(name, stats)
        if len(reports) > 1:
            click.echo()
            click.echo("  " + "─" * 52)

    if len(reports) > 1:
        total_raw = sum(s.get("raw_tokens", 0) for _, s in reports)
        total_served = sum(s.get("served_tokens", 0) for _, s in reports)
        total_queries = sum(s.get("queries", 0) for _, s in reports)
        total_saved = total_raw - total_served
        total_pct = int(total_saved / total_raw * 100) if total_raw > 0 else 0
        click.echo()
        click.echo(f"  {bold('Total')} {dim('across')} {value(str(len(reports)))} {dim('projects ·')} {value(f'{total_queries:,}')} {dim('queries')}")
        click.echo(f"  {success(f'Saved: {total_saved:,} tokens ({total_pct}%)')}")

    click.echo()


@main.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def clear(ctx: click.Context, yes: bool) -> None:
    """Clear all index data for the current project (vectors, FTS, graph, manifest)."""
    from context_engine.storage.local_backend import LocalBackend
    from context_engine.cli_style import warn, success, dim, value, section, animate, CHECK, DOT

    config = ctx.obj["config"]
    project_name = Path.cwd().name
    storage_dir = Path(config.storage_path) / project_name

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
    from context_engine.cli_style import success, warn, dim, value, section, animate, CHECK, CROSS, DOT

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
    from context_engine.cli_style import section, animate, value, dim, label, success, CHECK, DOT

    config = ctx.obj["config"]
    project_dir = str(Path.cwd())
    project_name = Path.cwd().name

    async def _search():
        from context_engine.storage.local_backend import LocalBackend
        from context_engine.indexer.embedder import Embedder
        from context_engine.retrieval.retriever import HybridRetriever

        storage_dir = Path(config.storage_path) / project_name
        if not (storage_dir / "vectors").exists():
            animate(["", f"  {DOT} {dim('Not indexed yet. Run:')} {label('cce init')}", ""])
            return

        backend = LocalBackend(base_path=str(storage_dir))
        embedder = Embedder(model_name=config.embedding_model)
        retriever = HybridRetriever(backend=backend, embedder=embedder)
        results = await retriever.retrieve(query, top_k=top_k)

        lines: list[str] = []
        lines.append("")
        lines.append(section(f"Search · {query}"))
        lines.append("")

        if not results:
            lines.append(f"    {DOT} {dim('No results found')}")
        else:
            # Compute tokens
            raw_tokens = 0
            served_tokens = 0
            seen_files: set[str] = set()
            for r in results:
                chunk_tokens = max(1, len(r.content) // 4)
                raw_tokens += chunk_tokens
                served_tokens += chunk_tokens
                seen_files.add(r.file_path)

            # Estimate full file tokens
            full_file_tokens = 0
            for fp in seen_files:
                full_path = Path(project_dir) / fp
                if full_path.exists():
                    try:
                        full_file_tokens += max(1, len(full_path.read_text(errors="ignore")) // 4)
                    except OSError:
                        pass

            for i, r in enumerate(results, 1):
                conf = r.metadata.get("confidence", "")
                conf_str = f"  {dim(f'({conf:.2f})')}" if isinstance(conf, (int, float)) else ""
                lines.append(f"    {label(str(i))}. {value(r.file_path)}:{r.start_line}-{r.end_line}{conf_str}")
                # Show first line of content
                first_line = r.content.strip().split("\n")[0][:80]
                lines.append(f"       {dim(first_line)}")

            lines.append("")
            lines.append(f"    {CHECK} {success(f'{len(results)} results')}  {dim(f'{served_tokens} tokens served vs {full_file_tokens} full file tokens')}")

            # Update stats
            stats_path = storage_dir / "stats.json"
            try:
                stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
            except (json.JSONDecodeError, OSError):
                stats = {}
            stats["queries"] = stats.get("queries", 0) + 1
            stats["raw_tokens"] = stats.get("raw_tokens", 0) + raw_tokens
            stats["served_tokens"] = stats.get("served_tokens", 0) + served_tokens
            stats.setdefault("full_file_tokens", 0)
            stats["full_file_tokens"] = max(stats["full_file_tokens"], full_file_tokens)
            stats_path.write_text(json.dumps(stats))

        lines.append("")
        animate(lines)

    asyncio.run(_search())


@main.command()
def uninstall() -> None:
    """Remove CCE from the current project (hooks, .mcp.json entry, CLAUDE.md block)."""
    from context_engine.cli_style import section, animate, value, dim, success, warn, CHECK, CROSS, DOT

    project_dir = Path.cwd()
    project_name = project_dir.name
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

    # Remove from .mcp.json
    mcp_path = project_dir / ".mcp.json"
    if mcp_path.exists():
        try:
            mcp_data = json.loads(mcp_path.read_text())
            servers = mcp_data.get("mcpServers", {})
            if "context-engine" in servers:
                del servers["context-engine"]
                mcp_path.write_text(json.dumps(mcp_data, indent=2) + "\n")
                lines.append(f"    {CROSS} {warn('Removed')} context-engine from .mcp.json")
            else:
                lines.append(f"    {DOT} {dim('No CCE entry in .mcp.json')}")
        except (json.JSONDecodeError, OSError):
            lines.append(f"    {DOT} {dim('Could not parse .mcp.json')}")
    else:
        lines.append(f"    {DOT} {dim('No .mcp.json found')}")

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

    lines.append("")
    lines.append(f"    {dim('Index data in ~/.cce is preserved.')}")
    lines.append(f"    {dim('Run')} {click.style('cce clear', fg='cyan')} {dim('to remove index data too.')}")
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


def savings_shortcut() -> None:
    """Entry point for the `cce-savings` shortcut command."""
    @click.command()
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    @click.option("--all", "all_projects", is_flag=True, help="Show all projects")
    def _cmd(as_json: bool, all_projects: bool) -> None:
        """Show CCE token savings — how much context compression is saving you."""
        project_path = Path.cwd() / PROJECT_CONFIG_NAME
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
    project_dir = Path.cwd()

    if port == 0:
        port = _find_free_port()

    # When CCE_DASHBOARD_TOKEN is set, append it to the URL so the dashboard
    # JS picks it up and includes it on mutating requests. Without the token
    # in the URL the page itself loads fine but Reindex / Clear / etc. would
    # 401 — most users would assume the dashboard was broken, so we surface
    # the URL with the token already attached.
    token = (_os.environ.get("CCE_DASHBOARD_TOKEN") or "").strip()
    from context_engine.cli_style import header, value, dim
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
    from context_engine.cli_style import header, dim, section, animate, value, success, warn, BULLET, BULLET_OFF

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
@click.pass_context
def sessions_prune(ctx: click.Context, threshold: int, keep: int) -> None:
    """Consolidate old session files into a single decisions log.

    Decisions from old sessions are appended to decisions_log.json and the
    source files are removed; the most-recent sessions (--keep) are kept
    verbatim. Decisions remain searchable via session_recall after pruning.
    """
    from context_engine.integration.session_capture import SessionCapture

    config = ctx.obj["config"]
    project_name = Path.cwd().name
    sessions_dir = Path(config.storage_path) / project_name / "sessions"
    if not sessions_dir.exists():
        click.echo(f"  {DOT} No sessions directory at {sessions_dir}")
        return
    capture = SessionCapture(sessions_dir=str(sessions_dir))
    summary = capture.prune_old_sessions(threshold=threshold, keep=keep)
    pruned = summary.get("pruned", 0)
    appended = summary.get("decisions_appended", 0)
    if pruned == 0:
        reason = summary.get("reason", "")
        click.echo(f"  {DOT} Nothing to prune ({reason}).")
    else:
        click.echo(
            f"  {CHECK} Pruned {pruned} session file(s); "
            f"archived {appended} decision(s) to decisions_log.json."
        )


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
    from context_engine.cli_style import success, warn, dim, value, CHECK, CROSS

    _showed_progress = False
    _bar_width = 30

    def progress_fn(current: int, total: int) -> None:
        nonlocal _showed_progress
        if not verbose and sys.stdout.isatty():
            filled = int(_bar_width * current / total) if total else 0
            bar = (
                click.style("█" * filled, fg="cyan") +
                click.style("░" * (_bar_width - filled), fg="bright_black")
            )
            pct = click.style(f"{int(100 * current / total) if total else 0}%", fg="bright_black")
            count = click.style(f"{current}/{total}", fg="white", bold=True)
            click.echo(f"\r    {bar}  {count} files  {pct}", nl=False)
            _showed_progress = True

    result = await run_indexing(
        config, project_dir, full=full, target_path=target_path,
        log_fn=log_fn, progress_fn=progress_fn,
    )

    if _showed_progress:
        click.echo()  # newline after progress bar

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
    project_name = Path(project_dir).name
    stats_path = Path(config.storage_path) / project_name / "stats.json"
    try:
        stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        stats = {}
    total_tokens = 0
    project_root = Path(project_dir)
    from context_engine.storage.local_backend import LocalBackend
    backend = LocalBackend(base_path=str(Path(config.storage_path) / project_name))
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
    from context_engine.storage.local_backend import LocalBackend
    from context_engine.indexer.embedder import Embedder
    from context_engine.retrieval.retriever import HybridRetriever
    from context_engine.compression.compressor import Compressor
    from context_engine.integration.mcp_server import ContextEngineMCP
    from context_engine.indexer.watcher import FileWatcher
    from context_engine.indexer.pipeline import run_indexing

    _log = logging.getLogger("context_engine.watcher")

    project_dir = str(Path.cwd())
    project_name = Path.cwd().name
    storage_base = Path(config.storage_path) / project_name
    backend = LocalBackend(base_path=str(storage_base))
    embedder = Embedder(model_name=config.embedding_model)
    retriever = HybridRetriever(backend=backend, embedder=embedder)
    compressor = Compressor(model=config.compression_model, cache=backend)
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

    watcher_label = " · live watcher active" if watcher else ""
    print(f"CCE ready · {project_name} · {chunk_count} chunks indexed{watcher_label}", file=sys.stderr)

    try:
        await mcp.run_stdio()
    finally:
        if watcher:
            watcher.stop()
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
