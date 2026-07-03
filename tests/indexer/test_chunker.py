# tests/indexer/test_chunker.py
import pytest
from context_engine.models import ChunkType
from context_engine.indexer.chunker import Chunker

@pytest.fixture
def chunker():
    return Chunker()

PYTHON_CODE = '''
class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b

def standalone_function(x):
    return x * 2
'''

JS_CODE = '''
function greet(name) {
    return `Hello, ${name}!`;
}

class Animal {
    constructor(name) {
        this.name = name;
    }
    speak() {
        return `${this.name} makes a noise.`;
    }
}
'''

def test_chunk_python_functions(chunker):
    chunks = chunker.chunk(PYTHON_CODE, file_path="calc.py", language="python")
    function_chunks = [c for c in chunks if c.chunk_type == ChunkType.FUNCTION]
    assert len(function_chunks) >= 2

def test_chunk_python_classes(chunker):
    chunks = chunker.chunk(PYTHON_CODE, file_path="calc.py", language="python")
    class_chunks = [c for c in chunks if c.chunk_type == ChunkType.CLASS]
    assert len(class_chunks) >= 1

def test_chunk_has_correct_metadata(chunker):
    chunks = chunker.chunk(PYTHON_CODE, file_path="calc.py", language="python")
    for chunk in chunks:
        assert chunk.file_path == "calc.py"
        assert chunk.language == "python"
        assert chunk.start_line >= 1
        assert chunk.end_line >= chunk.start_line
        assert chunk.id != ""
        assert chunk.content != ""

def test_chunk_javascript(chunker):
    chunks = chunker.chunk(JS_CODE, file_path="app.js", language="javascript")
    assert len(chunks) > 0
    function_chunks = [c for c in chunks if c.chunk_type == ChunkType.FUNCTION]
    assert len(function_chunks) >= 1

def test_chunk_unsupported_language_falls_back(chunker):
    chunks = chunker.chunk("some content here", file_path="data.txt", language="plaintext")
    assert len(chunks) == 1
    assert chunks[0].chunk_type == ChunkType.MODULE


MULTIBYTE_PYTHON = '''"""Módulo 🚀 — docstring with émojis and CJK: 日本語."""

def first():
    return "a"

def second():
    return "b"
'''


def test_chunk_multibyte_prefix_does_not_shift_offsets(chunker):
    """tree-sitter reports BYTE offsets on the utf-8 encoding; slicing the
    original str with them garbles every chunk after a multi-byte char.
    Chunk contents must exactly equal the function sources."""
    chunks = chunker.chunk(MULTIBYTE_PYTHON, file_path="emoji.py", language="python")
    function_chunks = [c for c in chunks if c.chunk_type == ChunkType.FUNCTION]
    assert [c.content for c in function_chunks] == [
        'def first():\n    return "a"',
        'def second():\n    return "b"',
    ]


def test_extract_imports_with_multibyte_prefix(chunker):
    """Byte offsets must also be handled in _parse_import_module — a CJK
    comment before the imports used to shift the sliced module names."""
    source = "# 日本語のコメント 🚀\nimport os\nfrom pathlib import Path\n\ndef main(): pass\n"
    _, imports = chunker.chunk_with_imports(source, file_path="main.py", language="python")
    assert imports == ["os", "pathlib"]


def test_chunk_multibyte_javascript_import(chunker):
    """JS string module specifiers after an emoji comment stay intact."""
    source = "// hello 🎉 world\nimport React from 'react';\nfunction App() { return 1; }\n"
    chunks, imports = chunker.chunk_with_imports(source, file_path="app.js", language="javascript")
    assert imports == ["react"]
    fn = [c for c in chunks if c.chunk_type == ChunkType.FUNCTION]
    assert fn and fn[0].content == "function App() { return 1; }"


def test_extract_imports_python():
    source = "import os\nfrom pathlib import Path\n\ndef main(): pass\n"
    chunker = Chunker()
    chunks, imports = chunker.chunk_with_imports(source, file_path="main.py", language="python")
    assert len(chunks) > 0
    assert "os" in imports
    assert "pathlib" in imports


def test_extract_imports_javascript():
    source = "import React from 'react';\nimport { useState } from 'react';\nfunction App() {}\n"
    chunker = Chunker()
    chunks, imports = chunker.chunk_with_imports(source, file_path="App.js", language="javascript")
    assert len(chunks) > 0
    assert "react" in imports


def test_chunk_still_works_without_imports():
    source = "def hello(): pass\n"
    chunker = Chunker()
    chunks = chunker.chunk(source, file_path="hello.py", language="python")
    assert len(chunks) == 1
