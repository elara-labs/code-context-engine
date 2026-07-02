"""Configuration loading — global + per-project with defaults."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


_CCE_HOME = Path.home() / ".cce"

DEFAULT_GLOBAL_PATH = _CCE_HOME / "config.yaml"
PROJECT_CONFIG_NAME = ".context-engine.yaml"

DEFAULT_IGNORE = [
    # Version control
    ".git", ".svn", ".hg",
    # Dependencies (JS, PHP, Python, Ruby, Go, Rust, Java, .NET)
    "node_modules", "vendor", "bower_components",
    ".pnpm-store", ".pnpm", ".yarn",
    ".venv", "venv", "env", ".env",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".cache",
    "Pods",  # iOS CocoaPods
    # Build output
    "dist", "build", "_build", "out", "target",
    "bin", "obj",  # .NET
    ".next", ".nuxt", ".output", ".vercel",
    ".turbo", ".parcel-cache",
    # IDE / editor
    ".idea", ".vscode", ".vs",
    # Coverage / test artifacts
    "coverage", ".coverage", "htmlcov", ".nyc_output",
    # OS files
    ".DS_Store",
    # Compiled / generated
    "__pycache__", ".sass-cache", ".gradle",
    # Infra
    ".terraform", ".vagrant",
    # Package locks (huge, not useful)
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "composer.lock", "poetry.lock",
    # Storage / logs
    "storage", "logs", "tmp", "temp",
]


@dataclass
class Config:
    # Compression
    compression_level: str = "standard"
    compression_model: str = "phi3:mini"
    # Ollama base URL for LLM-backed compression. Defaults to a local
    # install; point at a remote host (e.g. "http://nas.local:11434")
    # to share one Ollama across machines. CCE_OLLAMA_URL overrides this.
    ollama_url: str = "http://localhost:11434"

    # Output compression
    output_compression: str = "standard"  # off | lite | standard | max

    # Embedding
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Model used when the Ollama embedding backend is selected. Only
    # consulted if fastembed isn't installed or `CCE_EMBED_BACKEND=ollama`
    # forces the Ollama path. 768-dim default; switching this triggers a
    # full reindex because the vector store rejects dimension mismatches.
    ollama_embed_model: str = "nomic-embed-text"

    # Retrieval
    retrieval_confidence_threshold: float = 0.2
    retrieval_top_k: int = 20
    bootstrap_max_tokens: int = 10000

    # Indexer
    indexer_watch: bool = True
    indexer_debounce_ms: int = 500
    indexer_ignore: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    # When True, the indexer skips well-known credential filenames
    # (.env*, *.pem, secrets.yml, credentials.json, …) and redacts
    # AWS/GitHub/JWT/etc. patterns from the content of files it does
    # index. See indexer/secrets.py for the full pattern list. Default
    # True; users on non-sensitive corpora can opt out.
    indexer_redact_secrets: bool = True
    # When True, memory.db writes (decisions, code_areas, turn_summaries,
    # session rollups) get PII scrubbed before storage: emails, IPs,
    # credit cards (Luhn-validated), SSNs, phone numbers. Free-form
    # session text often captures user data — for regulated industries
    # this is the difference between "tool" and "compliance blocker".
    memory_redact_pii: bool = True
    # When True, every context_search call appends one JSON line to
    # {storage_base}/audit.log: timestamp, query length, top_k, served
    # chunks (file:start-end), score range, output compression level.
    # The query text is hashed (sha256, 12-char prefix) — the log is
    # for "what did Claude see when?" not "what did the user ask?".
    audit_log_enabled: bool = False

    # Pricing (for savings estimates)
    pricing_model: str = "opus"
    pricing_input: float | None = None   # $/1M input tokens override
    pricing_output: float | None = None  # $/1M output tokens override

    # Storage
    storage_path: str = str(_CCE_HOME / "projects")

    def detect_resource_profile(self) -> str:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        if ram_gb >= 32:
            return "full"
        if ram_gb >= 12:
            return "standard"
        return "light"


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


_EXPECTED_TYPES: dict[str, type | tuple[type, ...]] = {
    "compression_level": str,
    "compression_model": str,
    "ollama_url": str,
    "output_compression": str,
    "embedding_model": str,
    "ollama_embed_model": str,
    "retrieval_confidence_threshold": (int, float),
    "retrieval_top_k": int,
    "bootstrap_max_tokens": int,
    "indexer_watch": bool,
    "indexer_debounce_ms": int,
    "indexer_ignore": list,
    "indexer_redact_secrets": bool,
    "memory_redact_pii": bool,
    "audit_log_enabled": bool,
    "storage_path": str,
    "pricing_model": str,
    "pricing_input": (int, float, type(None)),
    "pricing_output": (int, float, type(None)),
}


def _apply_dict_to_config(config: Config, data: dict) -> None:
    mapping = {
        ("compression", "level"): "compression_level",
        ("compression", "model"): "compression_model",
        ("compression", "ollama_url"): "ollama_url",
        ("compression", "output"): "output_compression",
        ("embedding", "model"): "embedding_model",
        ("embedding", "ollama_model"): "ollama_embed_model",
        ("retrieval", "confidence_threshold"): "retrieval_confidence_threshold",
        ("retrieval", "top_k"): "retrieval_top_k",
        ("retrieval", "bootstrap_max_tokens"): "bootstrap_max_tokens",
        ("indexer", "watch"): "indexer_watch",
        ("indexer", "debounce_ms"): "indexer_debounce_ms",
        ("indexer", "ignore"): "indexer_ignore",
        ("indexer", "redact_secrets"): "indexer_redact_secrets",
        ("memory", "redact_pii"): "memory_redact_pii",
        ("audit", "enabled"): "audit_log_enabled",
        ("storage", "path"): "storage_path",
        ("pricing", "model"): "pricing_model",
        ("pricing", "input"): "pricing_input",
        ("pricing", "output"): "pricing_output",
    }
    for (section, key), attr in mapping.items():
        if section in data and isinstance(data[section], dict) and key in data[section]:
            value = data[section][key]
            expected = _EXPECTED_TYPES.get(attr)
            if expected is not None and not isinstance(value, expected):
                # `bool` is a subclass of `int`, so guard against that edge case.
                if expected is int and isinstance(value, bool):
                    raise ValueError(
                        f"Config {section}.{key} must be int, got bool ({value!r})"
                    )
                raise ValueError(
                    f"Config {section}.{key} must be "
                    f"{getattr(expected, '__name__', expected)}, "
                    f"got {type(value).__name__} ({value!r})"
                )
            # For ignore lists, merge with defaults instead of replacing.
            # This way user config adds to the defaults, not overrides them.
            if attr == "indexer_ignore" and isinstance(value, list):
                merged = list(DEFAULT_IGNORE)
                for item in value:
                    if item not in merged:
                        merged.append(item)
                setattr(config, attr, merged)
            else:
                setattr(config, attr, value)


def load_config(
    global_path: Path | None = None,
    project_path: Path | None = None,
) -> Config:
    global_path = global_path or DEFAULT_GLOBAL_PATH
    config = Config()

    global_data: dict = {}
    if global_path.exists():
        with open(global_path) as f:
            global_data = yaml.safe_load(f) or {}

    project_data: dict = {}
    if project_path and project_path.exists():
        with open(project_path) as f:
            project_data = yaml.safe_load(f) or {}

    merged = _deep_merge(global_data, project_data)
    _apply_dict_to_config(config, merged)
    return config


def resolve_ollama_url(config: Config) -> str:
    """Return the Ollama base URL with `CCE_OLLAMA_URL` env var overriding config.

    Env-var precedence lets a user point a single CCE install at a different
    Ollama (e.g. one running on another machine) without editing config files.
    Falls back to `config.ollama_url` (default `http://localhost:11434`).
    """
    env = os.environ.get("CCE_OLLAMA_URL", "").strip()
    return env or config.ollama_url
