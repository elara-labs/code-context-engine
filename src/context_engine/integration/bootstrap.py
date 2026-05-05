"""Bootstrap context builder — generates compressed project context for session start."""
from importlib.metadata import version as pkg_version

from context_engine.models import ConfidenceLevel

_CHARS_PER_TOKEN = 4


def _get_version() -> str:
    try:
        return pkg_version("code-context-engine")
    except Exception:
        return "unknown"


class BootstrapBuilder:
    def __init__(self, max_tokens: int = 10000) -> None:
        self._max_chars = max_tokens * _CHARS_PER_TOKEN

    def build(self, project_name, chunks=None, recent_commits=None,
              active_decisions=None, working_state=None, chunk_count=0,
              project_commands_text=None):
        sections = []
        ver = _get_version()
        status_line = f"CCE v{ver} · {chunk_count} chunks indexed" if chunk_count else f"CCE v{ver} · no chunks indexed yet"
        sections.append(f"## Project: {project_name}\n`{status_line}`")
        sections.append(self._build_architecture(chunks or []))
        sections.append(self._build_activity(recent_commits or []))
        if working_state:
            state_text = "\n".join(f"  {line}" for line in working_state)
            sections.append(f"### Working State\n{state_text}")
        if project_commands_text:
            sections.append(project_commands_text)
        if active_decisions:
            decisions_text = "\n".join(f"- {d}" for d in active_decisions)
            sections.append(f"### Active Context\n{decisions_text}")
        code_section = self._build_code_context(chunks or [])
        if code_section:
            sections.append(code_section)
        payload = "\n\n".join(sections)
        if len(payload) > self._max_chars:
            payload = payload[:self._max_chars] + "\n\n[Context truncated to fit token limit]"
        return payload

    def _build_architecture(self, chunks):
        high_conf = [c for c in chunks if ConfidenceLevel.from_score(c.confidence_score) == ConfidenceLevel.HIGH]
        if not high_conf:
            return "### Architecture\nNo indexed context available yet."
        by_file = {}
        for chunk in high_conf:
            by_file.setdefault(chunk.file_path, []).append(chunk)
        lines = ["### Architecture"]
        for file_path, file_chunks in sorted(by_file.items()):
            lines.append(f"\n**{file_path}:**")
            for chunk in file_chunks:
                text = chunk.compressed_content or chunk.content[:200]
                lines.append(f"- {text}")
        return "\n".join(lines)

    def _build_activity(self, commits):
        if not commits:
            return "### Recent Activity\nNo recent commits."
        lines = ["### Recent Activity"]
        for commit in commits[:10]:
            lines.append(f"- {commit}")
        return "\n".join(lines)

    def _build_code_context(self, chunks):
        medium_conf = [c for c in chunks if ConfidenceLevel.from_score(c.confidence_score) == ConfidenceLevel.MEDIUM]
        if not medium_conf:
            return ""
        lines = ["### Additional Context (may need drill-down)"]
        for chunk in medium_conf[:20]:
            text = chunk.compressed_content or chunk.content[:150]
            lines.append(f"- [{chunk.file_path}] {text}")
        return "\n".join(lines)
