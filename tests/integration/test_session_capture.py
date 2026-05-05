import pytest
from pathlib import Path
from context_engine.integration.session_capture import SessionCapture

@pytest.fixture
def capture(tmp_path):
    return SessionCapture(sessions_dir=str(tmp_path / "sessions"))

def test_start_session_creates_id(capture):
    session_id = capture.start_session(project_name="my-project")
    assert session_id is not None
    assert len(session_id) > 0

def test_record_decision(capture):
    sid = capture.start_session(project_name="test")
    capture.record_decision(sid, "Use Redis for caching", "Performance requirements")
    decisions = capture.get_decisions(sid)
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "Use Redis for caching"

def test_record_code_area(capture):
    sid = capture.start_session(project_name="test")
    capture.record_code_area(sid, "src/auth.py", "login function")
    areas = capture.get_code_areas(sid)
    assert len(areas) == 1

def test_end_session_saves_file(capture):
    sid = capture.start_session(project_name="test")
    capture.record_decision(sid, "Test decision", "Test reason")
    capture.end_session(sid)
    sessions_dir = Path(capture._sessions_dir)
    session_files = list(sessions_dir.glob("*.json"))
    assert len(session_files) == 1
