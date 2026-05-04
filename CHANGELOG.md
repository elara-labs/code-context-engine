# Changelog

All notable changes to Code Context Engine are documented here.

## [0.4.17] - 2026-05-04

### Added
- Configurable Ollama URL via `compression.ollama_url` config or `CCE_OLLAMA_URL` env var
- Codex detection via `~/.codex` with per-project TOML sections
- Friendly error message when `os.getcwd()` fails (macOS Full Disk Access)
- Streaming embed+ingest pipeline (memory usage capped at 50 files)
- SEO foundation: robots.txt, sitemap.xml, blog post
- Use cases section and quick start in README
- Downloads badge, MCP Registry badge
- Comparison page "honest limitations" section

### Changed
- README: "independently verified" replaced with "reproducible"
- Default pricing switched from Opus to Sonnet
- Roadmap moved shipped items to CHANGELOG
- Landing page synced to 94% retrieval, 0.90 recall numbers

### Fixed
- Windows TOML escaping for Codex config paths
- OpenCode icon on landing page

## Shipped Features

- Semantic indexing + hybrid retrieval + graph expansion
- Cross-session memory (decisions, code areas, session recall)
- Web dashboard with live charts
- Token savings tracking with dollar estimates
- Output compression (off / lite / standard / max)
- Content-hash embedding cache (96% hit rate on re-index)
- sqlite-vec migration (99% smaller install)
- Dynamic pricing from Anthropic docs
- 7-layer security (secrets, PII, path traversal, audit log)
- Clean uninstall (removes all CCE artifacts)
- AST-aware chunking for PHP, Go, Rust, Java (tree-sitter)
- Multi-editor support (Cursor, VS Code/Copilot, Gemini CLI, Codex, OpenCode)
- Reproducible benchmark suite (94% savings on FastAPI, per-layer breakdown)
- Session savings visibility (shown at every session start)

## [0.4.8] - 2026-05-01

### Added
- Platform badges (macOS, Linux, Windows) in README
- Per-platform system requirements table with Fedora/RHEL support

## [0.4.7] - 2026-05-01

### Fixed
- macOS CI: build pysqlite3 from source against Homebrew SQLite for loadable extension support
- Windows CI: platform-aware hook installer tests (double-quote quoting on Windows)
- Windows CI: handle spurious SIGINT during pytest teardown on GitHub Actions runners

## [0.4.6] - 2026-05-01

### Added
- CI testing on macOS and Windows (previously Linux only)
- `--yes` / `-y` flag and confirmation prompt on `cce uninstall`
- `cce search` now shows savings percentage and tracks full-file baseline correctly

### Changed
- Retrieval: keyword confidence weight increased from 30% to 40%, recency decreased from 20% to 10%
- File diversity cap: max 3 chunks per file in results, improving Recall@10 from 0.80 to 0.90
- Benchmark results updated: 94% retrieval savings (was 93%), 99.4% combined (was 99.3%)

### Fixed
- Publish workflow now creates GitHub Release inline (no longer depends on cross-workflow tag trigger)
- `cce search` stats tracking: savings are now cumulative per query instead of max-based

## [0.4.5] - 2026-05-01

### Added
- CHANGELOG.md and SECURITY.md for public release
- System requirements section in README (cmake, platform-specific build tools)
- `psutil` as declared dependency (was imported but undeclared)

### Fixed
- CONTRIBUTING.md: outdated "Kuzu graph" reference updated to "SQLite graph"
- CLI-Reference.md: all version examples updated from v0.4.0 to v0.4.4
- CLI-Reference.md: upgrade example showed a downgrade (0.4.0 to 0.3.2)
- pyproject.toml description aligned with README ("93% token savings, benchmarked on FastAPI")
- docs/benchmarks.md: replaced TBD placeholders with verified FastAPI benchmark data

### Changed
- Classifier bumped from "Alpha" to "Beta"
- `psutil` import in config.py no longer wrapped in try/except (now a real dependency)

## [0.4.4] - 2025-04-30

### Added
- Multi-editor auto-detection: `cce init` configures Claude Code, VS Code, Cursor, Gemini CLI, and Codex in one command
- Reproducible benchmark suite with FastAPI (93% savings, 20 queries)
- Session savings visibility at every Claude Code session start
- `cce upgrade` command with auto-detection of install method (uv, pipx, pip)
- `cce uninstall` for clean removal of all CCE artifacts
- OpenAI Codex CLI support

### Changed
- Promoted `aiohttp` from optional to core dependency (required for memory capture hooks)

## [0.4.3] - 2025-04-25

### Added
- Web dashboard with donut charts, file health, and session history (`cce dashboard`)
- Dollar cost estimates from live Anthropic pricing
- `cce savings --all` to view savings across all projects

## [0.4.2] - 2025-04-20

### Added
- AST-aware chunking for PHP, Go, Rust, Java via tree-sitter
- 7-layer security: secret file detection, content redaction (AWS keys, GitHub tokens, JWTs), PII scrubbing, path traversal protection

### Changed
- Migrated from LanceDB to sqlite-vec (99% smaller install, ~2 MB vs 217 MB)

## [0.4.1] - 2025-04-15

### Added
- Output compression with 4 levels (off, lite, standard, max)
- Content-hash embedding cache (96% hit rate on re-index)
- Deterministic grammar compression for memory entries

## [0.4.0] - 2025-04-10

### Added
- Cross-session memory: `record_decision`, `record_code_area`, `session_recall`
- Code graph with CALLS/IMPORTS edges and graph expansion
- `cce services` command for Ollama, dashboard, and MCP status
- Hybrid retrieval: vector + BM25 via Reciprocal Rank Fusion
- Confidence scoring with similarity (50%), keyword (30%), recency (20%) weights

## [0.3.0] - 2025-03-15

### Added
- Initial release with semantic indexing and MCP server
- Tree-sitter parsing for Python, JavaScript, TypeScript
- Git hooks for automatic re-indexing
- Token savings tracking
