"""Unit tests for `_resolve_language` — the indexer hook that lets users add
custom file-extension → language mappings via `indexer.extensions` in
`.context-engine.yaml`.
"""
from context_engine.indexer.pipeline import _resolve_language


def test_builtin_extension_resolves_to_known_language():
    assert _resolve_language(".py", {}) == "python"


def test_unknown_extension_falls_back_to_plaintext():
    assert _resolve_language(".xyz", {}) == "plaintext"


def test_custom_alias_overrides_builtin():
    # .h normally maps to c; custom mapping flips it to cpp.
    assert _resolve_language(".h", {".h": "cpp"}) == "cpp"


def test_custom_alias_for_unknown_extension():
    assert _resolve_language(".tpl", {".tpl": "html"}) == "html"


def test_custom_empty_value_means_plaintext():
    # User opts into indexing the file but knows there's no parser.
    assert _resolve_language(".liquid", {".liquid": ""}) == "plaintext"


def test_lookup_is_case_insensitive():
    # Extension comes from Path.suffix which preserves case (.HTML on Windows
    # mounts, .R for R files); custom map keys are normalised to lowercase
    # at config load time, so the lookup must lowercase the suffix too.
    assert _resolve_language(".HTML", {}) == "html"
    assert _resolve_language(".TPL", {".tpl": "html"}) == "html"
