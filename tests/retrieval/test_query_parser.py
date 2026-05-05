from context_engine.retrieval.query_parser import QueryParser, QueryIntent


def test_code_lookup_intent():
    parser = QueryParser()
    result = parser.parse("find the add function in math.py")
    assert result.intent == QueryIntent.CODE_LOOKUP
    assert "add" in result.keywords


def test_decision_recall_intent():
    parser = QueryParser()
    result = parser.parse("what did we decide about the auth system?")
    assert result.intent == QueryIntent.DECISION_RECALL


def test_architecture_intent():
    parser = QueryParser()
    result = parser.parse("how is the storage module structured?")
    assert result.intent == QueryIntent.ARCHITECTURE


def test_keyword_extraction():
    parser = QueryParser()
    result = parser.parse("show me the UserService class")
    assert "UserService" in result.keywords


def test_file_path_extraction():
    parser = QueryParser()
    result = parser.parse("what does src/auth/login.py do?")
    assert "src/auth/login.py" in result.file_hints
