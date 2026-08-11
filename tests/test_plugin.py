"""Tests for Agent Plugins generation and instruction template."""
import json
import yaml

from context_engine.cli import _discover_project_root
from context_engine.editors import generate_plugin, get_instructions_base


def test_instructions_base_loads_from_file():
    """Instruction template must load from data/instructions.md."""
    text = get_instructions_base()
    assert "context_search" in text
    assert "record_decision" in text
    assert "session_recall" in text
    assert len(text) > 100


def test_instructions_base_has_no_claude_specific_content():
    """The base template is agent-neutral (no Claude Code references)."""
    text = get_instructions_base()
    assert "Claude Code" not in text
    assert "CLAUDE.md" not in text


def test_generate_plugin_creates_structure(tmp_path):
    """generate_plugin writes the complete Agent Plugin directory."""
    out = tmp_path / "plugin"
    generate_plugin(out, version="1.2.3")

    assert (out / "plugin.json").exists()
    assert (out / "mcp.json").exists()
    assert (out / "skills" / "code-context" / "SKILL.md").exists()
    assert (out / "skills" / "code-context" / "references" / "tools.md").exists()


def test_plugin_json_schema(tmp_path):
    """plugin.json must conform to Agent Plugins v1.0.0."""
    out = tmp_path / "plugin"
    generate_plugin(out, version="1.2.3")

    data = json.loads((out / "plugin.json").read_text())
    assert data["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert data["name"] == "code-context-engine"
    assert data["version"] == "1.2.3"
    assert data["license"] == "MIT"


def test_mcp_json_schema(tmp_path):
    """mcp.json must declare stdio transport with uvx."""
    out = tmp_path / "plugin"
    generate_plugin(out, version="1.2.3")

    data = json.loads((out / "mcp.json").read_text())
    assert data["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    server = data["mcpServers"]["code-context-engine"]
    assert server["type"] == "stdio"
    assert server["command"] == "uvx"
    assert "cce" in server["args"]
    assert "serve" in server["args"]


def test_skill_md_frontmatter(tmp_path):
    """SKILL.md must have valid Agent Skills frontmatter."""
    out = tmp_path / "plugin"
    generate_plugin(out, version="1.2.3")

    content = (out / "skills" / "code-context" / "SKILL.md").read_text()
    # Split frontmatter
    assert content.startswith("---\n")
    parts = content.split("---\n", 2)
    fm = yaml.safe_load(parts[1])
    assert fm["name"] == "code-context"
    assert len(fm["description"]) > 10
    assert fm["license"] == "MIT"
    assert fm["metadata"]["version"] == "1.2.3"


def test_skill_md_body_has_instructions(tmp_path):
    """SKILL.md body must contain the instruction template content."""
    out = tmp_path / "plugin"
    generate_plugin(out, version="1.2.3")

    content = (out / "skills" / "code-context" / "SKILL.md").read_text()
    assert "context_search" in content
    assert "record_decision" in content


def test_generate_plugin_overwrites_existing(tmp_path):
    """Regenerating into the same directory overwrites cleanly."""
    out = tmp_path / "plugin"
    generate_plugin(out, version="1.0.0")
    generate_plugin(out, version="2.0.0")

    data = json.loads((out / "plugin.json").read_text())
    assert data["version"] == "2.0.0"


# ── Project auto-discovery tests ─────────────────────────────────────


def test_discover_project_root_finds_git(tmp_path):
    """Walk-up finds .git directory."""
    project = tmp_path / "myproject"
    project.mkdir()
    (project / ".git").mkdir()
    subdir = project / "src" / "deep"
    subdir.mkdir(parents=True)

    assert _discover_project_root(subdir) == project


def test_discover_project_root_finds_context_engine_yaml(tmp_path):
    """Walk-up finds .context-engine.yaml."""
    project = tmp_path / "myproject"
    project.mkdir()
    (project / ".context-engine.yaml").write_text("indexer:\n  watch: true\n")
    subdir = project / "src"
    subdir.mkdir()

    assert _discover_project_root(subdir) == project


def test_discover_project_root_prefers_context_engine_yaml(tmp_path):
    """When .context-engine.yaml is found first on walk-up, it wins."""
    root = tmp_path / "root"
    root.mkdir()
    (root / ".git").mkdir()
    inner = root / "inner"
    inner.mkdir()
    (inner / ".context-engine.yaml").write_text("")

    assert _discover_project_root(inner) == inner


def test_discover_project_root_returns_none(tmp_path):
    """Returns None when no project markers found."""
    bare = tmp_path / "bare"
    bare.mkdir()

    assert _discover_project_root(bare) is None
