"""AST-aware code chunking using tree-sitter."""
import hashlib
import threading

import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
import tree_sitter_php as tsphp
import tree_sitter_go as tsgo
import tree_sitter_rust as tsrust
import tree_sitter_java as tsjava
import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser

from context_engine.models import Chunk, ChunkType

_FUNCTION_TYPES = {
    "function_definition", "function_declaration",  # Python, PHP, JS
    "method_definition", "method_declaration",       # JS/TS, PHP/Go/Java/C#
    "arrow_function",                                # JS/TS
    "function_item",                                 # Rust
    "local_function_statement",                      # C#
}
_CLASS_TYPES = {
    "class_definition", "class_declaration",       # Python, JS/TS, PHP, Java, C#
    "struct_declaration", "interface_declaration",  # C#
    "record_declaration", "enum_declaration",       # Java, C#
    "type_declaration",                             # Go (struct/interface)
    "struct_item", "impl_item", "enum_item",        # Rust
}
_IMPORT_TYPES = {
    "import_statement", "import_from_statement",  # Python
    "import_declaration",                          # TypeScript, Go, Java
    "use_declaration",                             # PHP, Rust
    "using_directive",                             # C#
}

def _node_text(src_bytes: bytes, node) -> str:
    """Slice a node's source from the utf-8 BYTES tree-sitter parsed.

    node.start_byte / node.end_byte are byte offsets into the encoded
    source, not str indices — slicing the original str garbles content
    whenever a multi-byte character precedes the node.
    """
    return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


_LANGUAGES = {
    "python": Language(tspython.language()),
    "javascript": Language(tsjavascript.language()),
    "typescript": Language(tstypescript.language_typescript()),
    "tsx": Language(tstypescript.language_tsx()),
    "php": Language(tsphp.language_php()),
    "go": Language(tsgo.language()),
    "rust": Language(tsrust.language()),
    "java": Language(tsjava.language()),
    "csharp": Language(tscsharp.language()),
}


class Chunker:
    # A single Chunker is shared across the indexing run, and the pipeline
    # fans chunk_with_imports() out over asyncio.to_thread — up to 50 files
    # of the same language parse concurrently. tree_sitter.Parser holds
    # mutable C parse state and is documented as unsafe to use from multiple
    # threads at once; sharing one Parser per language raced on that state
    # and corrupted memory (SIGSEGV/SIGBUS, issue #113). Parsers are cheap to
    # build, so cache them per worker thread via threading.local instead —
    # each thread gets its own Parser and never contends with another.
    def __init__(self) -> None:
        self._local = threading.local()

    def _get_parser(self, language: str) -> Parser | None:
        if language not in _LANGUAGES:
            return None
        parsers = getattr(self._local, "parsers", None)
        if parsers is None:
            parsers = {}
            self._local.parsers = parsers
        parser = parsers.get(language)
        if parser is None:
            parser = Parser(_LANGUAGES[language])
            parsers[language] = parser
        return parser

    def chunk(self, source: str, file_path: str, language: str) -> list[Chunk]:
        parser = self._get_parser(language)
        if parser is None:
            return [self._fallback_chunk(source, file_path, language)]
        # tree-sitter parses the utf-8 BYTES and reports byte offsets.
        # Encode once and slice the bytes — slicing the original str with
        # byte offsets silently garbles every chunk after the first
        # multi-byte character (emoji, CJK, accents).
        src_bytes = source.encode("utf-8")
        tree = parser.parse(src_bytes)
        chunks = []
        self._walk(tree.root_node, src_bytes, file_path, language, chunks)
        if not chunks:
            return [self._fallback_chunk(source, file_path, language)]
        return chunks

    def _walk(self, node, src_bytes, file_path, language, chunks):
        if node.type in _FUNCTION_TYPES:
            chunks.append(self._node_to_chunk(node, src_bytes, file_path, language, ChunkType.FUNCTION))
        elif node.type in _CLASS_TYPES:
            chunks.append(self._node_to_chunk(node, src_bytes, file_path, language, ChunkType.CLASS))
        for child in node.children:
            self._walk(child, src_bytes, file_path, language, chunks)

    def _node_to_chunk(self, node, src_bytes, file_path, language, chunk_type):
        content = _node_text(src_bytes, node)
        start_line = node.start_point.row + 1
        end_line = node.end_point.row + 1
        chunk_id = hashlib.sha256(
            f"{file_path}:{start_line}:{end_line}:{content[:100]}".encode()
        ).hexdigest()[:16]
        return Chunk(
            id=chunk_id, content=content, chunk_type=chunk_type,
            file_path=file_path, start_line=start_line, end_line=end_line, language=language,
        )

    def chunk_with_imports(
        self, source: str, file_path: str, language: str
    ) -> tuple[list[Chunk], list[str]]:
        chunks = self.chunk(source, file_path, language)
        imports = self._extract_imports(source, language)
        return chunks, imports

    def _extract_imports(self, source: str, language: str) -> list[str]:
        parser = self._get_parser(language)
        if parser is None:
            return []
        # Same byte-offset contract as chunk(): slice the encoded bytes,
        # never the str (multi-byte chars shift str indices).
        src_bytes = source.encode("utf-8")
        tree = parser.parse(src_bytes)
        imports: list[str] = []
        self._walk_imports(tree.root_node, src_bytes, language, imports)
        return list(dict.fromkeys(imports))  # deduplicate while preserving order

    def _walk_imports(self, node, src_bytes, language, imports):
        if node.type in _IMPORT_TYPES:
            module = self._parse_import_module(node, src_bytes, language)
            if module:
                imports.append(module)
        for child in node.children:
            self._walk_imports(child, src_bytes, language, imports)

    def _parse_import_module(self, node, src_bytes, language) -> str | None:
        if node.type == "import_statement":
            # Python: "import os" or "import os.path"
            # Also handles JS/TS: "import React from 'react'" (string child present)
            for child in node.children:
                if child.type == "string":
                    # JavaScript/TypeScript import with string module specifier
                    raw = _node_text(src_bytes, child).strip("'\"")
                    return raw.split("/")[0] if not raw.startswith("@") else "/".join(raw.split("/")[:2])
                if child.type in ("dotted_name", "aliased_import"):
                    # Python bare import
                    name = _node_text(src_bytes, child)
                    name = name.split(" as ")[0].strip()
                    return name.split(".")[0]
        elif node.type == "import_from_statement":
            # Python: "from pathlib import Path"
            for child in node.children:
                if child.type in ("dotted_name", "relative_import"):
                    name = _node_text(src_bytes, child).strip()
                    name = name.lstrip(".")
                    if name:
                        return name.split(".")[0]
        elif node.type == "using_directive":
            # C#: "using System.Collections.Generic;" — take the root namespace
            # segment, mirroring the Python dotted-name convention below.
            for child in node.children:
                if child.type in ("qualified_name", "identifier"):
                    name = _node_text(src_bytes, child).strip()
                    return name.split(".")[0]
        elif node.type == "import_declaration":
            # TypeScript (tree-sitter-typescript): "import React from 'react'"
            for child in node.children:
                if child.type == "string":
                    raw = _node_text(src_bytes, child).strip("'\"")
                    return raw.split("/")[0] if not raw.startswith("@") else "/".join(raw.split("/")[:2])
        return None

    def _fallback_chunk(self, source, file_path, language):
        chunk_id = hashlib.sha256(f"{file_path}:module".encode()).hexdigest()[:16]
        lines = source.strip().split("\n")
        return Chunk(
            id=chunk_id, content=source, chunk_type=ChunkType.MODULE,
            file_path=file_path, start_line=1, end_line=len(lines), language=language,
        )
