import pytest

@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory for testing."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def main():\n    print('hello')\n")
    (src_dir / "utils.py").write_text("def helper(x):\n    return x + 1\n")
    return tmp_path
