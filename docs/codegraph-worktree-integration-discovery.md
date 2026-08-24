[← Back to README](../README.md)

# CodeGraph Worktree Integration Discovery

## Overview

This document records Phase 0 discovery for integrating Code Context Engine
(CCE) with CodeGraph using shared repository context plus per-worktree overlays.
It is evidence for the staged implementation plan, not production behavior.

## Current CCE Storage Identity

CCE currently keys project storage by the absolute project checkout path.

File references:

- `src/context_engine/utils.py`
- `src/context_engine/integration/mcp_server.py`
- `tests/test_project_storage_dir.py`

Observed behavior:

- `project_storage_dir(config, project_dir)` resolves `project_dir`, hashes that
  absolute path, and stores data under `<storage_path>/<basename>-<6hex>`.
- `ContextEngineMCP.__init__` calls `project_storage_dir(config, Path.cwd())`.
- Existing migration only renames legacy basename-only storage to the
  path-hashed slug.

Consequence:

- Two linked Git worktrees of the same repository get different complete CCE
  storage directories.
- CCE does not yet model a shared repository identity plus a separate worktree
  identity.

## CodeGraph Worktree Mismatch Behavior

CodeGraph already detects a dangerous shared-index case.

File references:

- `/home/joe/Webstorm_Projects/codegraph/src/sync/worktree.ts`
- `/home/joe/Webstorm_Projects/codegraph/__tests__/worktree-detection.test.ts`
- `/home/joe/Webstorm_Projects/codegraph/src/directory.ts`

Observed behavior:

- CodeGraph resolves an index by walking upward to the nearest `.codegraph/`
  directory.
- `gitWorktreeRoot(dir)` runs `git rev-parse --show-toplevel`.
- `gitCommonDir(dir)` runs `git rev-parse --git-common-dir`.
- `detectWorktreeIndexMismatch(startPath, indexRoot)` warns when a command runs
  inside one Git worktree but uses another worktree's `.codegraph/` index from
  the same Git common directory.
- The warning says results may reflect another branch and suggests
  `codegraph init -i` for a worktree-local index.

Implication:

- CCE must not symlink or blindly reuse another worktree's mutable
  `.codegraph/` directory.
- Shared base reuse is only safe when CCE explicitly records the base revision
  and overlays worktree changes.

## CodeGraph Machine-Readable Interface

Best stable interface order for the MVP:

1. CodeGraph package API when available to the CCE process through a Node
   subprocess wrapper.
2. Structured CodeGraph CLI JSON output.
3. Human CLI output only for `explore`, as a temporary read-only fallback.

Evidence:

- CodeGraph README documents package API usage:
  `CodeGraph.init`, `CodeGraph.open`, `searchNodes`, `getCallers`,
  `buildContext`, and `getImpactRadius`.
- `src/index.ts` exports `CodeGraph`, `getDatabasePath`, `DatabaseConnection`,
  `QueryBuilder`, `findNearestCodeGraphRoot`, and related types.
- `codegraph query`, `codegraph files`, `codegraph callers`,
  `codegraph callees`, `codegraph impact`, and `codegraph affected` expose
  `--json`.
- `codegraph explore` is the primary MCP-equivalent high-level tool, but the
  CLI path is Markdown text rather than JSON in the inspected source.

Constraints:

- CCE must invoke CodeGraph without `shell=True`.
- CCE must use bounded timeouts and stdout limits.
- Any Node wrapper must be treated as a boundary process, not imported into the
  Python runtime directly.

## CodeGraph Index Identity And Freshness Signals

File references:

- `/home/joe/Webstorm_Projects/codegraph/__tests__/status-json.test.ts`
- `/home/joe/Webstorm_Projects/codegraph/src/directory.ts`

Observed behavior:

- `codegraph status --json` exposes `initialized`, `version`, `indexPath`,
  `lastIndexed`, and an `index.state` value.
- Tests cover `index.state == "complete"` after clean full index and
  `"indexing"` for interrupted index work.
- CodeGraph uses `.codegraph/codegraph.db` as the SQLite index.
- `CODEGRAPH_DIR` can point one checkout to a different local index directory,
  but it remains a per-project-root directory name, not a shared base overlay
  model.

MVP freshness use:

- Treat `version`, `indexPath`, `lastIndexed`, and `index.state` as status
  evidence.
- Treat non-complete or missing status as degraded provider state.
- Keep CCE overlay freshness independent from CodeGraph base freshness.

## Stable Symbol Enumeration

CodeGraph exposes enough read APIs for shared-base exploration:

- `searchNodes(query, options)` for symbol search.
- `getCallers(node_id)` and `getCallees(node_id)` for relationships.
- `getImpactRadius(node_id, depth)` for blast radius.
- `files --json` for indexed file inventory.

Limitations:

- CodeGraph node IDs are database-local. They must not be used as stable
  identities across a shared base and a worktree overlay.
- CCE overlay merge should use logical symbol identity: qualified name, kind,
  path, and signature where available.

## Reusable Base Index Safety

Safe reuse:

- Query a CodeGraph index only as the shared base for the base revision it
  represents.
- Store CCE metadata that ties that base to repository identity, base SHA,
  CodeGraph index path, CodeGraph version, and freshness status.

Unsafe reuse:

- Do not point every worktree at another checkout's mutable `.codegraph/`.
- Do not return CodeGraph base source for a file known modified or deleted in
  the worktree overlay.

## Selected Git Base-Ref Strategy

Use this deterministic hierarchy:

1. Explicit configured base SHA/ref.
2. Upstream branch merge-base.
3. Unambiguous `origin/main`, `origin/master`, `main`, or `master` merge-base.
4. Current `HEAD` for dirty-only worktrees.
5. No base SHA when no safe base can be established.

Rules:

- Never silently compare against an unrelated branch.
- Include staged, unstaged, committed branch delta, untracked, deleted, and
  renamed files in the diff model.
- Treat renames as old-path tombstone plus new-path addition for the MVP.

## Limitations Requiring CCE Overlay Analysis

CCE must own overlay semantics because CodeGraph has no inspected stable API for
querying a shared base plus a separate worktree overlay.

Required CCE responsibilities:

- Worktree identity detection.
- Worktree diff detection.
- Overlay semantic chunks for changed and added files.
- Tombstones for deleted and renamed-away files.
- Modified-path shadowing so stale base chunks do not leak into answers.
- Logical-symbol merge rules for structural overlay results.

## Stage 1 Implementation Notes

Recommended first production branch:

- Add a CCE Git repository context module.
- Add repository/worktree IDs based on realpath-normalized Git common directory
  and worktree root.
- Add base SHA resolution using the selected hierarchy.
- Add a diff model using Git plumbing.
- Add unit tests with real temporary Git repositories and linked worktrees.
