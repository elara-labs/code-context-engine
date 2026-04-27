"""Per-project memory store — SQLite tables backing cross-session recall.

This package introduces the new memory.db storage. The legacy JSON-per-session
capture path in `context_engine.integration.session_capture` continues to work
unchanged; it is retired in a follow-up PR once hooks land.
"""
