"""Configuration loading — global + per-project with defaults."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml


_NEW_HOME = Path.home() / ".cce"
_OLD_HOME = Path.home() / ".claude-context-engine"

# Use the old directory if it exists and the new one does not, so existing
# installs keep working without a manual migration step.
_CCE_HOME = _OLD_HOME if (_OLD_HOME.exists() and not _NEW_HOME.exists()) else _NEW_HOME

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

    # Output compression
    output_compression: str = "standard"  # off | lite | standard | max

    # Embedding
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Retrieval
    retrieval_confidence_threshold: float = 0.2
    retrieval_top_k: int = 20
    bootstrap_max_tokens: int = 10000

    # Indexer
    indexer_watch: bool = True
    indexer_debounce_ms: int = 500
    indexer_ignore: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))

    # Storage
    storage_path: str = str(_CCE_HOME / "projects")

    def detect_resource_profile(self) -> str:
        try:
            import psutil
            ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        except ImportError:
            ram_gb = 16
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
    "output_compression": str,
    "embedding_model": str,
    "retrieval_confidence_threshold": (int, float),
    "retrieval_top_k": int,
    "bootstrap_max_tokens": int,
    "indexer_watch": bool,
    "indexer_debounce_ms": int,
    "indexer_ignore": list,
    "storage_path": str,
}


def _apply_dict_to_config(config: Config, data: dict) -> None:
    mapping = {
        ("compression", "level"): "compression_level",
        ("compression", "model"): "compression_model",
        ("compression", "output"): "output_compression",
        ("embedding", "model"): "embedding_model",
        ("retrieval", "confidence_threshold"): "retrieval_confidence_threshold",
        ("retrieval", "top_k"): "retrieval_top_k",
        ("retrieval", "bootstrap_max_tokens"): "bootstrap_max_tokens",
        ("indexer", "watch"): "indexer_watch",
        ("indexer", "debounce_ms"): "indexer_debounce_ms",
        ("indexer", "ignore"): "indexer_ignore",
        ("storage", "path"): "storage_path",
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
