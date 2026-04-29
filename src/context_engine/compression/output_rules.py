"""Output compression rules — reduces Claude's output token usage via style directives."""

LEVELS = ("off", "lite", "standard", "max")

# Estimated baseline reply size (tokens) when no compression is active.
# Used to estimate output_compression savings: the MCP server can't see
# Claude's actual reply length, so we assume an average per affected
# response and apply the advertised reduction. Tunable — the renderer
# footnotes the value so users can interpret the estimate.
ESTIMATED_AVG_REPLY_TOKENS = 500

# Advertised output-token reduction per level. Sourced from the level
# descriptions ("~65% savings", "~75% savings"). `lite` has no advertised
# number; we use a conservative 20% based on how much filler/hedging
# typically lives in default-mode replies.
ADVERTISED_PCT = {
    "off": 0.0,
    "lite": 0.20,
    "standard": 0.65,
    "max": 0.75,
}

_RULES = {
    "lite": (
        "## Output Compression: Lite\n"
        "Respond concisely. Rules:\n"
        "- Remove filler words (just, really, basically, actually, simply)\n"
        "- Remove hedging (I think, it seems, perhaps, might want to)\n"
        "- No pleasantries (Sure!, Happy to help, Great question)\n"
        "- No trailing summaries — the diff/output speaks for itself\n"
        "- Keep full grammar and articles\n"
        "- Code blocks, paths, commands, URLs: NEVER compress"
    ),
    "standard": (
        "## Output Compression: Standard\n"
        "Respond in compressed style. Rules:\n"
        "- Drop articles (a, an, the) in prose\n"
        "- Use sentence fragments over full sentences\n"
        "- Use short synonyms (fix > resolve, check > investigate, big > large)\n"
        "- Pattern: [thing] [action] [reason]. [next step].\n"
        "- No filler, hedging, pleasantries, or trailing summaries\n"
        "- No restating what the user said\n"
        "- One-line explanations unless detail is asked for\n"
        "- Code blocks, paths, commands, URLs, errors: NEVER compress\n"
        "- Security warnings and destructive action confirmations: use full clarity"
    ),
    "max": (
        "## Output Compression: Max\n"
        "Respond in telegraphic style. Rules:\n"
        "- Drop articles, pronouns, conjunctions where meaning survives\n"
        "- Abbreviate common terms: DB, auth, config, fn, dep, impl, req, resp, init\n"
        "- Use arrows for causality: → (leads to), ← (caused by)\n"
        "- Use symbols: + (add), - (remove), ~ (change), ! (warning), ? (unclear)\n"
        "- Max 1-2 sentences per explanation\n"
        "- Pattern: [thing] → [action]. [reason].\n"
        "- Code blocks, paths, commands, URLs, errors: NEVER compress\n"
        "- Security warnings and destructive action confirmations: use full clarity"
    ),
}


def get_output_rules(level: str) -> str | None:
    """Return the output compression rules for the given level, or None if off."""
    if level == "off":
        return None
    return _RULES.get(level)


def get_level_description(level: str) -> str:
    """Return a human-readable description of the compression level."""
    descriptions = {
        "off": "No output compression — Claude responds normally",
        "lite": "Removes filler, hedging, and pleasantries. Keeps full grammar.",
        "standard": "Drops articles, uses fragments, short synonyms. ~65% output token savings.",
        "max": "Telegraphic style with abbreviations and symbols. ~75% output token savings.",
    }
    return descriptions.get(level, "Unknown level")
