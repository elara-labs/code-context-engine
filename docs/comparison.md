# How CCE Compares to Alternatives

An honest look at how Code Context Engine stacks up against other tools that
help AI assistants understand your codebase. Every tool makes trade-offs.
This page explains ours.

## Quick comparison

| | CCE | Cursor (built-in) | Aider (repo-map) | Continue.dev | Greptile |
|---|---|---|---|---|---|
| **Editor support** | Any (Claude Code, Cursor, VS Code, Gemini CLI, Codex, OpenCode) | Cursor only | CLI only | VS Code, JetBrains | SaaS API |
| **Code stays local** | Yes | No (cloud indexed) | Yes | Depends on LLM | No (cloud) |
| **Setup** | `cce init` + `cce serve` | Zero (built in) | Zero (built in) | Extension install | API key + config |
| **Token savings tracking** | Yes (per-query metrics) | No | No | No | N/A |
| **Cross-session memory** | Yes (decisions, code areas) | No | No | No | No |
| **Indexing approach** | AST + hybrid vector/BM25 | Proprietary embeddings | Tree-sitter outlines | Embeddings | Cloud embeddings |
| **Cost** | Free, open source | $20/mo+ (Pro) | Free, open source | Free, open source | Paid |
| **Best for** | Multi-editor teams, privacy-sensitive, token-conscious | Cursor-only users who want zero setup | CLI power users | IDE-centric workflows | Large teams, monorepos |

## Detailed comparison

### vs Cursor's built-in indexing

**Where Cursor wins:**
Zero setup. It just works. Open a project, start coding, context is there.
Cursor's indexing is deeply integrated into the editor and invisible to the user.
Their proprietary embeddings are trained specifically for code retrieval and likely
outperform open-source embedding models (like the one CCE uses) on retrieval quality.
They also have access to editor state (open tabs, cursor position, recent edits) which
CCE cannot see. For Cursor-only users who don't mind cloud indexing, there's no reason to add CCE.

**Where CCE wins:**
Editor independence. If you switch between Claude Code and Cursor (or use VS Code,
Gemini CLI, Codex, or OpenCode), Cursor's index doesn't follow you. CCE works across all of
them with a single index. Your code never leaves your machine. And you get
measurable token savings with per-query tracking.

### vs Aider's repo-map

**Where Aider wins:**
Lighter weight and battle-tested. Aider's repo-map uses tree-sitter to extract
function/class signatures and builds a concise map without any embedding model. No
60 MB model download, no ONNX runtime, no SQLite indices. It's elegant, fast, and
has been refined over thousands of real-world coding sessions. Aider also optimizes
the map dynamically per conversation turn, which CCE does not do.

**Where CCE wins:**
Deeper retrieval. Aider's repo-map gives the LLM a structural overview but sends
full files when they're relevant. CCE returns specific chunks with confidence
scores, meaning the LLM gets only the code it needs. For large files (500+ lines),
this difference is significant. CCE also tracks token savings so you know the actual
cost reduction.

### vs Continue.dev

**Where Continue wins:**
Deep IDE integration and real-time awareness. Continue lives in your editor with
native access to open files, terminal output, diagnostics, and editor state. Its
context system understands what you're looking at right now, not just what's in the
repo. It also supports multiple LLM providers with a unified interface, and has a
larger community and contributor base than CCE.

**Where CCE wins:**
Continue's context is session-scoped. Close the editor and it's gone. CCE's
cross-session memory preserves decisions, architectural context, and code area
annotations across sessions. CCE also works outside the IDE (Claude Code CLI,
Gemini CLI, Codex, OpenCode).

### vs Greptile

**Where Greptile wins:**
Scale and integration. Greptile is a cloud service built for large teams and monorepos.
It handles cross-repo indexing at a scale that a local tool can't match, integrates
into PR review workflows, and provides team-wide code understanding out of the box.
Their retrieval likely benefits from larger, more expensive embedding models that would
be impractical to run locally. For organizations with thousands of repos and dedicated
budgets, Greptile solves a fundamentally different problem than CCE.

**Where CCE wins:**
Privacy and cost. CCE is free, open source, and your code never leaves your machine.
There's no API key, no per-query billing, no data retention policy to worry about.
For individual developers or small teams working on proprietary code, local-first
matters.

## When to use CCE

CCE is the right choice when:

- You use multiple AI coding tools (Claude Code, Cursor, VS Code, Gemini CLI)
- Your code is proprietary and can't be sent to cloud services
- You want to measure and reduce token costs
- You need context that persists across sessions
- You want an open source tool you can inspect and modify

## When to use something else

- **Cursor only, cloud is fine:** Use Cursor's built-in indexing. Zero setup wins.
- **CLI only, lightweight preferred:** Aider's repo-map is simpler and lighter.
- **Large org, many repos:** Greptile's cloud scale makes more sense.
- **IDE-centric, real-time context:** Continue.dev's editor integration is deeper.

We'd rather you pick the right tool than pick ours for the wrong reasons.

## CCE's honest limitations

- **Single-repo benchmark.** Our 94% number comes from FastAPI only. Different codebases (monorepos, many small files, heavily templated code) may see different results. We're adding more benchmarks.
- **Open-source embedding model.** We use a local model (~60 MB) that runs on CPU. Cloud tools using larger models (e.g., OpenAI text-embedding-3-large) likely have better retrieval quality on ambiguous queries.
- **No editor state awareness.** CCE can't see your open tabs, cursor position, or recent edits. Tools integrated into editors (Cursor, Continue) have richer real-time context.
- **Setup required.** It's one command, but it's not zero. Built-in tools win on frictionlessness.
- **Young project.** CCE has fewer contributors, fewer battle-tested edge cases, and less production mileage than established tools like Aider or Cursor.
