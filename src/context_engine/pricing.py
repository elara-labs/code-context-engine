"""Model pricing for savings estimates.

Anthropic pricing is fetched from docs and cached. Other providers use
static fallbacks that are updated with releases.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from context_engine.config import Config

_CCE_HOME = Path.home() / ".cce"
_CACHE_PATH = _CCE_HOME / "pricing_cache.json"
_CACHE_TTL = 7 * 24 * 3600  # 7 days
_DOCS_URL = "https://docs.anthropic.com/en/docs/about-claude/models"


class ModelPricing(TypedDict):
    input: float   # $/1M input tokens
    output: float  # $/1M output tokens


# Anthropic fallback (used when fetch fails and no cache exists)
_ANTHROPIC_FALLBACK: dict[str, ModelPricing] = {
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 0.80, "output": 4.0},
}

# Static pricing for non-Anthropic models. Updated with releases.
# Keys are lowercase, matched against config pricing.model.
_STATIC_PRICING: dict[str, ModelPricing] = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3": {"input": 2.0, "output": 8.0},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
    "codex-mini": {"input": 1.50, "output": 6.0},
    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    # Anthropic (duplicated here so static lookup works without fetching)
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 0.80, "output": 4.0},
}

# Backward compat alias
_FALLBACK = _ANTHROPIC_FALLBACK


def _parse_html(html: str) -> dict[str, ModelPricing] | None:
    """Parse per-family input + output pricing from Anthropic docs HTML table."""
    input_pricing: dict[str, float] = {}
    output_pricing: dict[str, float] = {}

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    col_families: list[str | None] = []

    for row_html in rows:
        cells = re.findall(
            r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, re.DOTALL | re.IGNORECASE
        )

        # Header row: extract column → family mapping
        families_in_row: list[str | None] = []
        has_model = False
        for cell in cells:
            m = re.search(r"Claude\s+(Opus|Sonnet|Haiku)", cell, re.IGNORECASE)
            if m:
                families_in_row.append(m.group(1).lower())
                has_model = True
            else:
                families_in_row.append(None)

        if has_model and sum(1 for f in families_in_row if f) >= 2:
            col_families = families_in_row
            continue

        if not col_families:
            continue

        # Detect whether this is an input or output pricing row
        is_input = any("input" in c.lower() and "tok" in c.lower() for c in cells)
        is_output = any("output" in c.lower() and "tok" in c.lower() for c in cells)
        target = None
        if is_input and not is_output:
            target = input_pricing
        elif is_output and not is_input:
            target = output_pricing

        if target is not None:
            for i, cell in enumerate(cells):
                if i < len(col_families) and col_families[i]:
                    m = re.search(r"\$(\d+(?:\.\d+)?)", cell)
                    if m:
                        family = col_families[i]
                        if family not in target:
                            target[family] = float(m.group(1))
            if target is output_pricing:
                col_families = []

    if not input_pricing:
        return None

    result: dict[str, ModelPricing] = {}
    for family in input_pricing:
        result[family] = {
            "input": input_pricing[family],
            "output": output_pricing.get(family, input_pricing[family] * 5),
        }
    return result


def _fetch() -> dict[str, ModelPricing] | None:
    try:
        import httpx

        resp = httpx.get(_DOCS_URL, follow_redirects=True, timeout=5.0)
        if resp.status_code != 200:
            return None
        return _parse_html(resp.text)
    except Exception:
        return None


def _load_cache() -> dict[str, ModelPricing] | None:
    try:
        if not _CACHE_PATH.exists():
            return None
        data = json.loads(_CACHE_PATH.read_text())
        if time.time() - data.get("ts", 0) < _CACHE_TTL:
            raw = data.get("pricing")
            if not raw:
                return None
            # Migrate flat input-only cache to ModelPricing format
            first = next(iter(raw.values()), None)
            if isinstance(first, (int, float)):
                return {
                    k: {"input": v, "output": v * 5}
                    for k, v in raw.items()
                }
            return raw
    except Exception:
        pass
    return None


def _save_cache(pricing: dict[str, ModelPricing]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps({"ts": time.time(), "pricing": pricing}))
    except Exception:
        pass


def get_model_pricing() -> dict[str, ModelPricing]:
    """Return {model: {input, output}} pricing per 1M tokens.

    Merges static pricing for all providers with live Anthropic pricing
    (fetched from docs, cached 7 days). Live data wins for Anthropic models.
    """
    result = dict(_STATIC_PRICING)
    cached = _load_cache()
    if cached:
        result.update(cached)
        return result
    fetched = _fetch()
    if fetched:
        _save_cache(fetched)
        result.update(fetched)
        return result
    return result


def list_available_models() -> list[str]:
    """Return sorted list of all model keys with known pricing."""
    return sorted(get_model_pricing().keys())


def resolve_pricing(config: Config) -> tuple[str, ModelPricing]:
    """Return (model_label, {input, output}) respecting config overrides.

    Priority:
    1. Explicit pricing.input / pricing.output in config (full override)
    2. Lookup by pricing.model in the merged pricing table
    3. Fallback to Opus
    """
    model = config.pricing_model.lower()
    all_pricing = get_model_pricing()
    default = all_pricing.get("opus", {"input": 15.0, "output": 75.0})
    base = all_pricing.get(model, default)

    resolved: ModelPricing = {
        "input": config.pricing_input if config.pricing_input is not None else base["input"],
        "output": config.pricing_output if config.pricing_output is not None else base["output"],
    }

    # Label reflects whether user overrode rates
    if config.pricing_input is not None or config.pricing_output is not None:
        label = f"{model} (custom)"
    else:
        label = model

    return label, resolved
