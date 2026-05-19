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
# number; we use a conservative 25% based on filler removal + code diff rules.
# The code output rules (show diffs, not full files) add ~5-10% on top of
# prose compression since code responses are a large share of output tokens.
ADVERTISED_PCT = {
    "off": 0.0,
    "lite": 0.25,
    "standard": 0.70,
    "max": 0.80,
}

# Code output rules — appended to all non-off levels to reduce code token waste.
_CODE_RULES = (
    "\n\n## Code Output Rules\n"
    "When suggesting code changes:\n"
    "- Show ONLY the changed lines with minimal surrounding context (3 lines above/below)\n"
    "- Use edit format: file path, then the specific change. Never rewrite entire files.\n"
    "- If multiple changes in one file, show each change separately, not the whole file\n"
    "- Never echo back unchanged code the user already has\n"
    "- For new files, show the full file. For edits, show only what changes."
)

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
        + _CODE_RULES
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
        + _CODE_RULES
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
        + _CODE_RULES
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
        "lite": "Removes filler, hedging, and pleasantries. Diff-only for code. ~25% savings.",
        "standard": "Drops articles, uses fragments, short synonyms. Diff-only for code. ~70% savings.",
        "max": "Telegraphic style with abbreviations and symbols. Diff-only for code. ~80% savings.",
    }
    return descriptions.get(level, "Unknown level")


# ── Instruction-file blocks ──────────────────────────────────────────
# These go into CLAUDE.md, AGENTS.md, .cursorrules, etc. so they apply
# to the entire session, not just CCE tool responses.

_INSTRUCTION_OUTPUT_STYLES = {
    "lite": """\
### Output style

Respond concisely. Remove filler words (just, really, basically, actually,
simply), hedging (I think, it seems, perhaps), and pleasantries (Sure!,
Happy to help, Great question). No trailing summaries. Keep full grammar.

When suggesting code changes, show only the changed lines with 3 lines of
context. Never rewrite entire files. For new files, show the full file.
For edits, show only what changes.""",

    "standard": """\
### Output style

Respond in compressed style. Drop articles (a, an, the) in prose. Use
sentence fragments over full sentences. Use short synonyms (fix not resolve,
check not investigate). Pattern: [thing] [action] [reason]. [next step].
No filler, hedging, pleasantries, trailing summaries, or restating what
the user said. One sentence if one sentence is enough.

When suggesting code changes, show only the changed lines with 3 lines of
context. Never rewrite entire files. Multiple changes in one file: show each
change separately. Never echo back unchanged code the user already has.

Code blocks, file paths, commands, error messages: always written in full.
Security warnings and destructive action confirmations: use full clarity.""",

    "max": """\
### Output style

Respond in telegraphic style. Drop articles, pronouns, conjunctions where
meaning survives. Abbreviate common terms: DB, auth, config, fn, dep, impl,
req, resp, init. Use arrows for causality: X → Y. Use symbols: + (add),
- (remove), ~ (change), ! (warning). Max 1-2 sentences per explanation.
Pattern: [thing] → [action]. [reason].

When suggesting code changes, show only changed lines. Never rewrite files.
Never echo back unchanged code.

Code blocks, paths, commands, errors: always full.
Security warnings and destructive actions: full clarity, drop compression.""",
}


def get_instruction_output_block(level: str) -> str:
    """Return the output style block for instruction files, or empty if off."""
    return _INSTRUCTION_OUTPUT_STYLES.get(level, "")
