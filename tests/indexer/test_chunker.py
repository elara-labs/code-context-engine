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

CSHARP_CODE = '''
using System;
using System.Collections.Generic;

namespace Shop.Payments
{
    public interface IPaymentGateway
    {
        Receipt Charge(decimal amount);
    }

    public record Receipt(string Id, decimal Amount);

    public enum PaymentStatus
    {
        Pending,
        Completed
    }

    public struct Money
    {
        public decimal Amount;
    }

    public class StripeGateway : IPaymentGateway
    {
        public Receipt Charge(decimal amount)
        {
            decimal ApplyFee(decimal baseAmount)
            {
                return baseAmount * 1.029m;
            }
            return new Receipt(Guid.NewGuid().ToString(), ApplyFee(amount));
        }
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

def test_chunk_csharp_functions(chunker):
    chunks = chunker.chunk(CSHARP_CODE, file_path="Payments.cs", language="csharp")
    function_chunks = [c for c in chunks if c.chunk_type == ChunkType.FUNCTION]
    # Charge method, interface method signature, and ApplyFee local function
    assert len(function_chunks) >= 3

def test_chunk_csharp_types(chunker):
    chunks = chunker.chunk(CSHARP_CODE, file_path="Payments.cs", language="csharp")
    class_chunks = [c for c in chunks if c.chunk_type == ChunkType.CLASS]
    # class, interface, record, enum, struct each become their own chunk
    assert len(class_chunks) >= 5
    contents = " ".join(c.content for c in class_chunks)
    assert "interface IPaymentGateway" in contents
    assert "record Receipt" in contents
    assert "enum PaymentStatus" in contents
    assert "struct Money" in contents
    assert "class StripeGateway" in contents

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


def test_extract_imports_csharp():
    chunker = Chunker()
    chunks, imports = chunker.chunk_with_imports(CSHARP_CODE, file_path="Payments.cs", language="csharp")
    assert len(chunks) > 0
    # Both using directives resolve to their root namespace and deduplicate
    assert imports == ["System"]


def test_chunk_still_works_without_imports():
    source = "def hello(): pass\n"
    chunker = Chunker()
    chunks = chunker.chunk(source, file_path="hello.py", language="python")
    assert len(chunks) == 1
