"""AST-aware code chunking using tree-sitter."""
import hashlib

import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
import tree_sitter_php as tsphp
import tree_sitter_go as tsgo
import tree_sitter_rust as tsrust
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from context_engine.models import Chunk, ChunkType

_FUNCTION_TYPES = {
    "function_definition", "function_declaration",  # Python, PHP, JS
    "method_definition", "method_declaration",       # JS/TS, PHP/Go/Java
    "arrow_function",                                # JS/TS
    "function_item",                                 # Rust
}
_CLASS_TYPES = {
    "class_definition", "class_declaration",  # Python, JS/TS, PHP, Java
    "type_declaration",                        # Go (struct/interface)
    "struct_item", "impl_item", "enum_item",   # Rust
}
_IMPORT_TYPES = {
    "import_statement", "import_from_statement",  # Python
    "import_declaration",                          # TypeScript, Go, Java
    "use_declaration",                             # PHP, Rust
}

_LANGUAGES = {
    "python": Language(tspython.language()),
    "javascript": Language(tsjavascript.language()),
    "typescript": Language(tstypescript.language_typescript()),
    "tsx": Language(tstypescript.language_tsx()),
    "php": Language(tsphp.language_php()),
    "go": Language(tsgo.language()),
    "rust": Language(tsrust.language()),
    "java": Language(tsjava.language()),
}


class Chunker:
    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}

    def _get_parser(self, language: str) -> Parser | None:
        if language not in _LANGUAGES:
            return None
        if language not in self._parsers:
            parser = Parser(_LANGUAGES[language])
            self._parsers[language] = parser
        return self._parsers[language]

    def chunk(self, source: str, file_path: str, language: str) -> list[Chunk]:
        parser = self._get_parser(language)
        if parser is None:
            return [self._fallback_chunk(source, file_path, language)]
        tree = parser.parse(source.encode("utf-8"))
        chunks = []
        self._walk(tree.root_node, source, file_path, language, chunks)
        if not chunks:
            return [self._fallback_chunk(source, file_path, language)]
        return chunks

    def _walk(self, node, source, file_path, language, chunks):
        if node.type in _FUNCTION_TYPES:
            chunks.append(self._node_to_chunk(node, source, file_path, language, ChunkType.FUNCTION))
        elif node.type in _CLASS_TYPES:
            chunks.append(self._node_to_chunk(node, source, file_path, language, ChunkType.CLASS))
        for child in node.children:
            self._walk(child, source, file_path, language, chunks)

    def _node_to_chunk(self, node, source, file_path, language, chunk_type):
        content = source[node.start_byte:node.end_byte]
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
        tree = parser.parse(source.encode("utf-8"))
        imports: list[str] = []
        self._walk_imports(tree.root_node, source, language, imports)
        return list(dict.fromkeys(imports))  # deduplicate while preserving order

    def _walk_imports(self, node, source, language, imports):
        if node.type in _IMPORT_TYPES:
            module = self._parse_import_module(node, source, language)
            if module:
                imports.append(module)
        for child in node.children:
            self._walk_imports(child, source, language, imports)

    def _parse_import_module(self, node, source, language) -> str | None:
        if node.type == "import_statement":
            # Python: "import os" or "import os.path"
            # Also handles JS/TS: "import React from 'react'" (string child present)
            for child in node.children:
                if child.type == "string":
                    # JavaScript/TypeScript import with string module specifier
                    raw = source[child.start_byte:child.end_byte].strip("'\"")
                    return raw.split("/")[0] if not raw.startswith("@") else "/".join(raw.split("/")[:2])
                if child.type in ("dotted_name", "aliased_import"):
                    # Python bare import
                    name = source[child.start_byte:child.end_byte]
                    name = name.split(" as ")[0].strip()
                    return name.split(".")[0]
        elif node.type == "import_from_statement":
            # Python: "from pathlib import Path"
            for child in node.children:
                if child.type in ("dotted_name", "relative_import"):
                    name = source[child.start_byte:child.end_byte].strip()
                    name = name.lstrip(".")
                    if name:
                        return name.split(".")[0]
        elif node.type == "import_declaration":
            # TypeScript (tree-sitter-typescript): "import React from 'react'"
            for child in node.children:
                if child.type == "string":
                    raw = source[child.start_byte:child.end_byte].strip("'\"")
                    return raw.split("/")[0] if not raw.startswith("@") else "/".join(raw.split("/")[:2])
        return None

    def _fallback_chunk(self, source, file_path, language):
        chunk_id = hashlib.sha256(f"{file_path}:module".encode()).hexdigest()[:16]
        lines = source.strip().split("\n")
        return Chunk(
            id=chunk_id, content=source, chunk_type=ChunkType.MODULE,
            file_path=file_path, start_line=1, end_line=len(lines), language=language,
        )
