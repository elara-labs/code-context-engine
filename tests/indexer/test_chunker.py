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


HTML_WITH_BLOCKS = """<!DOCTYPE html>
<html>
<head><title>Page</title></head>
<body>
<h1>Hello</h1>
<script>
  console.log("a");
</script>
<style>
  body { color: red; }
</style>
</body>
</html>
"""

HTML_PLAIN = """<!DOCTYPE html>
<html><body><h1>Just text</h1><p>no script or style here</p></body></html>
"""


def test_chunk_html_extracts_script_and_style(chunker):
    chunks = chunker.chunk(HTML_WITH_BLOCKS, file_path="page.html", language="html")
    types = [c.chunk_type for c in chunks]
    # script and style become MODULE chunks
    assert types.count(ChunkType.MODULE) >= 2
    contents = [c.content for c in chunks if c.chunk_type == ChunkType.MODULE]
    assert any("console.log" in c for c in contents)
    assert any("color: red" in c for c in contents)


def test_chunk_html_without_blocks_falls_back_to_whole_file(chunker):
    chunks = chunker.chunk(HTML_PLAIN, file_path="plain.html", language="html")
    # No script/style → fallback path returns single MODULE chunk for the file
    assert len(chunks) == 1
    assert chunks[0].chunk_type == ChunkType.MODULE
    assert "Just text" in chunks[0].content
