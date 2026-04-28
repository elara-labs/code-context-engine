"""Dynamic model pricing — fetched from Anthropic docs, cached locally."""
import json
import re
import time
from pathlib import Path

_CCE_HOME = Path.home() / ".cce"
_CACHE_PATH = _CCE_HOME / "pricing_cache.json"
_CACHE_TTL = 7 * 24 * 3600  # 7 days
_DOCS_URL = "https://docs.anthropic.com/en/docs/about-claude/models"

# Used only when fetch fails and no cache exists
_FALLBACK: dict[str, float] = {
    "opus": 5.0,
    "sonnet": 3.0,
    "haiku": 1.0,
}


def _parse_html(html: str) -> dict[str, float] | None:
    """Parse per-family input pricing from Anthropic docs HTML table."""
    pricing: dict[str, float] = {}

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

        # Pricing row: extract $ amounts per column
        if col_families and any(
            "input" in c.lower() and "tok" in c.lower() for c in cells
        ):
            for i, cell in enumerate(cells):
                if i < len(col_families) and col_families[i]:
                    m = re.search(r"\$(\d+(?:\.\d+)?)", cell)
                    if m:
                        family = col_families[i]
                        if family not in pricing:
                            pricing[family] = float(m.group(1))
            col_families = []

    return pricing if pricing else None


def _fetch() -> dict[str, float] | None:
    try:
        import httpx

        resp = httpx.get(_DOCS_URL, follow_redirects=True, timeout=5.0)
        if resp.status_code != 200:
            return None
        return _parse_html(resp.text)
    except Exception:
        return None


def _load_cache() -> dict[str, float] | None:
    try:
        if not _CACHE_PATH.exists():
            return None
        data = json.loads(_CACHE_PATH.read_text())
        if time.time() - data.get("ts", 0) < _CACHE_TTL:
            return data.get("pricing")
    except Exception:
        pass
    return None


def _save_cache(pricing: dict[str, float]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps({"ts": time.time(), "pricing": pricing}))
    except Exception:
        pass


def get_model_pricing() -> dict[str, float]:
    """Return {family: input_price_per_1M_tokens}. Cached 7 days."""
    cached = _load_cache()
    if cached:
        return cached
    fetched = _fetch()
    if fetched:
        _save_cache(fetched)
        return fetched
    return dict(_FALLBACK)
