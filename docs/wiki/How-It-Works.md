# How It Works

CCE is a pipeline: index your code once, then retrieve only the relevant pieces on each query. This page explains every stage.

---

## 1. Code Indexing

When you run `cce init` or `cce index`, CCE walks your repository file by file:

1. **Hash each file.** Files that haven't changed since the last run are skipped.
2. **Parse into chunks.** For supported languages, CCE uses Tree-sitter to split code into semantic units: functions, classes, and modules. For other file types, it falls back to line-based chunking.
3. **Embed each chunk.** The embedding model (`BAAI/bge-small-en-v1.5`) converts each chunk into a 384-dimensional vector.
4. **Store in three indexes:** the vector store (sqlite-vec), the full-text search store (SQLite FTS5), and the code graph (SQLite).

The git `post-commit` hook installed by `cce init` runs `cce index` automatically after every commit, so the index stays current without any manual effort.

---

## 2. Semantic Chunking

Flat text search misses a lot. If you search for "calculate shipping", you want the `calculate_shipping` function — not every file that mentions the word.

CCE uses Tree-sitter to parse code into its actual structure:

```text
payments.py (800 lines, ~12k tokens)
  → calculate_shipping()        chunk  (lines 45-90)
  → validate_address()          chunk  (lines 92-130)
  → ShippingMethod               class  (lines 132-200)
  → ...
```

Each chunk is embedded and stored independently. On retrieval, Claude gets the `calculate_shipping` function (600 tokens) rather than the entire file (12,000 tokens).

**Supported languages with AST-aware chunking:** Python, JavaScript, TypeScript (JSX/TSX), PHP, Go, Rust, Java.

Other file types use line-based chunking with reasonable defaults.

---

## 3. Hybrid Retrieval

Every `context_search` query runs two searches in parallel and merges the results:

**Vector search.** The query is embedded and compared against all chunk embeddings using cosine similarity. This finds semantically related code even if the exact words differ. Stored in sqlite-vec.

**Full-text search.** BM25 keyword ranking over raw chunk content. This finds exact matches that vector search might rank lower. Stored in SQLite FTS5.

The two result lists are merged using **Reciprocal Rank Fusion (RRF)** — a well-studied algorithm for combining rankings from different sources without needing to tune weights. Chunks that rank highly in both searches get the highest final scores.

---

## 4. Graph-Aware Expansion

After the hybrid search produces a ranked list, CCE walks the code graph one hop.

The graph stores relationships between code elements: which functions call which, which files import which. This is built during indexing using Tree-sitter's import and call detection.

**The expansion step:**

1. Take the top 3 result files from the primary search.
2. Look up their graph neighbors via CALLS and IMPORTS edges.
3. For each related file (up to 2), run a filtered vector search to fetch 1-2 relevant chunks from that file.
4. Append these bonus chunks to the result set with a small confidence penalty (0.85x).

**Example:**

```text
Query: "validate user token"
Primary result:  auth.py:validate_token      (vector match, confidence 0.91)
Graph expansion: utils.py:decode_jwt         (auth.py CALLS utils.py)
                 db.py:fetch_user_by_id      (auth.py CALLS db.py)
```

Claude gets the full call context without needing a follow-up query.

---

## 5. Confidence Scoring and Filtering

Every chunk gets a final confidence score combining:

- Vector similarity distance (closer = higher score)
- Keyword overlap with the query
- RRF rank position
- Path penalty: test files, docs, and plan files are deprioritised

Only chunks above the configured `confidence_threshold` (default 0.5) are returned.

---

## 6. Compression

CCE reduces chunk size before including it in the response:

**Truncation fallback (always available).** Keeps the function signature and docstring, drops the body:

```python
# Original (50 lines)
def calculate_shipping(order, warehouse, method="standard"):
    """Calculate shipping cost based on order weight and location."""
    total_weight = sum(item.weight * item.quantity for item in order.items)
    # ... 47 more lines

# Compressed
def calculate_shipping(order, warehouse, method="standard"):
    """Calculate shipping cost based on order weight and location."""
```

**LLM summarization (requires Ollama).** If Ollama is running locally, CCE uses `phi3:mini` (or your configured model) to produce a higher-quality summary that preserves more semantics than truncation.

Compression is controlled by the `compression.level` config: `minimal`, `standard`, or `full`.

---

## 7. Overflow References

When search results exceed the token budget (`max_tokens`, default 8000), CCE does not silently drop the remaining chunks. Instead, it lists them as compact references:

```text
2 more result(s) available (not shown to save tokens):
  expand_chunk(chunk_id="abc123")  → payments.py:45 (confidence: 0.82)
  expand_chunk(chunk_id="def456")  → orders.py:112  (confidence: 0.71)
```

Claude sees what exists and can call `expand_chunk` to retrieve any of them individually, paying only for what it actually needs.

---

## 8. Output Compression

Beyond compressing code chunks, CCE also compresses Claude's own responses. The `set_output_compression` MCP tool sets the verbosity level:

| Level | Style | Typical savings |
|-------|-------|-----------------|
| `off` | Full Claude output | 0% |
| `lite` | No filler or hedging | ~30% |
| `standard` | Shorter phrasing and fragments | ~65% |
| `max` | Telegraphic style | ~75% |

Code blocks, file paths, commands, and error messages are never compressed regardless of level.

---

## 9. Cross-Session Memory

CCE persists two types of context across sessions:

**Decisions.** When Claude makes an architectural decision (which library to use, why a particular pattern was chosen), it calls `record_decision`. This is stored in SQLite and recalled at the start of future sessions via `session_recall`.

**Code areas.** When Claude works in a specific file, it calls `record_code_area` with a description of what was done. This builds a history of which parts of the codebase have been touched and why.

---

## Token Budget Example

```text
Session start:      Project overview               ->  10k tokens
Search:             "Find payment processing"      ->   800 tokens
Graph expansion:    utils.py + db.py bonus chunks  ->   400 tokens
Overflow refs:      3 more results listed          ->    90 tokens
                                                    --------
                                                    11.3k tokens

Without CCE:        Read payments.py + shipping.py ->  45k tokens
```
