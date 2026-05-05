import pytest
from context_engine.compression.quality import QualityChecker

@pytest.fixture
def checker():
    return QualityChecker()

# ── Existing tests ────────────────────────────────────────────────────────────

def test_passes_when_identifiers_preserved(checker):
    original = "def calculate_total(items, tax_rate): return sum(items) * (1 + tax_rate)"
    summary = "calculate_total takes items and tax_rate, returns the sum of items with tax applied."
    assert checker.check(original, summary) is True

def test_fails_when_identifiers_missing(checker):
    original = "def calculate_total(items, tax_rate): return sum(items) * (1 + tax_rate)"
    summary = "A function that computes a value."
    assert checker.check(original, summary) is False

def test_extracts_identifiers_from_code(checker):
    code = "class UserService:\n    def get_user(self, user_id): pass"
    identifiers = checker.extract_identifiers(code)
    assert "UserService" in identifiers
    assert "get_user" in identifiers
    assert "user_id" in identifiers

# ── Summary preserving >40% identifiers → passes ─────────────────────────────

def test_passes_at_exact_threshold(checker):
    # Build a summary that preserves exactly 40% of whatever the checker extracts,
    # rather than hard-coding an assumed count that can drift with the impl.
    original = (
        "def process_batch(records, schema, config, output, limit):\n"
        "    pass"
    )
    identifiers = checker.extract_identifiers(original)
    assert len(identifiers) > 0, "Need at least one identifier to test threshold"

    # Include exactly ceil(40%) of the identifiers so we land on or just above threshold
    import math
    keep_count = math.ceil(len(identifiers) * 0.4)
    kept = identifiers[:keep_count]
    summary = "summary mentions " + " ".join(kept)
    assert checker.check(original, summary) is True

def test_passes_when_all_identifiers_preserved(checker):
    original = "def foo(bar, baz):\n    pass"
    identifiers = checker.extract_identifiers(original)
    summary = " ".join(identifiers)
    assert checker.check(original, summary) is True

# ── Summary preserving <40% identifiers → fails ──────────────────────────────

def test_fails_just_below_threshold(checker):
    # 5 identifiers; only 1 in summary (20%) → should fail
    original = (
        "def process_batch(records, schema, config, output, limit):\n"
        "    pass"
    )
    summary = "processes records"
    assert checker.check(original, summary) is False

def test_fails_when_summary_empty_but_code_has_identifiers(checker):
    original = "def important_function(param_one, param_two):\n    pass"
    assert checker.check(original, "") is False

# ── Code with no identifiers → always passes ─────────────────────────────────

def test_passes_when_code_has_no_identifiers(checker):
    assert checker.check("42 + 3.14", "some result") is True

def test_passes_when_code_is_empty(checker):
    assert checker.check("", "anything") is True

def test_passes_when_both_empty(checker):
    assert checker.check("", "") is True

# ── Identifier extraction details ────────────────────────────────────────────

def test_extracts_camel_case_class_names(checker):
    code = "class DataProcessor:\n    pass"
    assert "DataProcessor" in checker.extract_identifiers(code)

def test_extracts_self_attributes(checker):
    code = "def __init__(self):\n    self.connection_pool = None"
    assert "connection_pool" in checker.extract_identifiers(code)

def test_skips_short_identifiers(checker):
    # 'id' is only 2 chars — below _MIN_IDENTIFIER_LEN of 3
    code = "def get(id):\n    pass"
    assert "id" not in checker.extract_identifiers(code)

def test_skips_reserved_names(checker):
    # self and cls are filtered in the parameter extraction loop.
    # Note: None/True/False are NOT filtered by the CamelCase regex pass,
    # so they can appear in results — that is a known impl behaviour, not tested here.
    code = "def foo(self, cls, bar):\n    pass"
    ids = checker.extract_identifiers(code)
    assert "self" not in ids
    assert "cls" not in ids

def test_camelcase_regex_captures_none_true_false(checker):
    # Documents the known behaviour: the CamelCase pattern matches None/True/False
    # because they start with a capital letter and are ≥3 chars.
    # This test exists to catch if the behaviour changes in a future fix.
    code = "x = None\ny = True\nz = False"
    ids = checker.extract_identifiers(code)
    assert "None" in ids
    assert "True" in ids
    assert "False" in ids

def test_extract_identifiers_returns_sorted_list(checker):
    code = "def zebra(alpha, mango):\n    pass"
    ids = checker.extract_identifiers(code)
    assert ids == sorted(ids)

def test_extracts_params_with_type_hints(checker):
    code = "def connect(host: str, port: int = 8080):\n    pass"
    ids = checker.extract_identifiers(code)
    assert "host" in ids
    assert "port" in ids

# ── Case-insensitive matching ─────────────────────────────────────────────────

def test_check_is_case_insensitive(checker):
    # quality.py lowercases both sides before comparing
    original = "def CalculateTotal(Items, TaxRate):\n    pass"
    summary = "calculatetotal uses items and taxrate"
    assert checker.check(original, summary) is True

# ── Edge cases ────────────────────────────────────────────────────────────────

def test_passes_with_whitespace_only_code(checker):
    assert checker.check("   \n\t  ", "summary") is True

def test_long_summary_still_fails_if_no_identifiers_match(checker):
    original = "def secret_algo(hidden_param, private_key):\n    pass"
    summary = " ".join(["word"] * 100)
    assert checker.check(original, summary) is False
