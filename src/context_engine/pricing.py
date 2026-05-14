"""Dynamic model pricing — fetched from Anthropic docs, cached locally."""
import json
import re
import time
from pathlib import Path
from typing import TypedDict

_CCE_HOME = Path.home() / ".cce"
_CACHE_PATH = _CCE_HOME / "pricing_cache.json"
_CACHE_TTL = 7 * 24 * 3600  # 7 days
_DOCS_URL = "https://docs.anthropic.com/en/docs/about-claude/models"


class ModelPricing(TypedDict):
    input: float   # $/1M input tokens
    output: float  # $/1M output tokens


# Used only when fetch fails and no cache exists
_FALLBACK: dict[str, ModelPricing] = {
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 0.80, "output": 4.0},
}

# Flat input-only fallback kept for backward compat with existing cache files
_FALLBACK_INPUT: dict[str, float] = {
    "opus": 15.0,
    "sonnet": 3.0,
    "haiku": 0.80,
}


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
    """Return {family: {input, output}} pricing per 1M tokens. Cached 7 days."""
    cached = _load_cache()
    if cached:
        return cached
    fetched = _fetch()
    if fetched:
        _save_cache(fetched)
        return fetched
    return dict(_FALLBACK)
