"""Regression tests for XSS escaping in the embedded dashboard page.

The dashboard JS builds HTML via string concatenation from API-sourced
values (file paths, session projects, decision text, session ids). Every
such interpolation must go through _esc() for HTML context, and event
handlers must not inline API strings into onclick attributes (JS-in-HTML
context) — they must use data-* attributes + addEventListener instead.

There is no browser test harness, so these tests assert on the generated
page source: the known-bad patterns must be gone and the escaped/safe
forms present. Regression for 2026-07-03 security review.
"""

from context_engine.dashboard._page import PAGE_HTML


# ── Sessions tab (loadSessions) ───────────────────────


def test_sessions_tab_escapes_project_name():
    # Old: '+(s.project||s.id)+'  — unescaped stored XSS vector.
    assert "'+(s.project||s.id)+'" not in PAGE_HTML
    assert "_esc(s.project||s.id)" in PAGE_HTML


def test_sessions_tab_escapes_decision_text():
    # Old: '<div class="decision-item">'+d.decision+'</div>'
    assert "'+d.decision+'" not in PAGE_HTML
    assert '<div class="decision-item">\'+_esc(d.decision)+\'</div>' in PAGE_HTML


# ── Files table (renderFiles) ─────────────────────────


def test_files_table_escapes_path_in_title_and_text():
    # Old: title="'+f.path+'">'+f.path+'
    assert "'+f.path+'" not in PAGE_HTML
    assert 'title="\'+_esc(f.path)+\'"' in PAGE_HTML


def test_files_table_escapes_status():
    assert "'+f.status+'" not in PAGE_HTML
    assert "_esc(f.status)" in PAGE_HTML


def test_files_table_uses_data_attributes_not_inline_onclick():
    # Old: onclick="reindexFile('+JSON.stringify(f.path)+')" — JSON.stringify
    # covers JS-string context but not the surrounding HTML attribute context.
    assert "JSON.stringify(f.path)" not in PAGE_HTML
    assert "onclick=\"reindexFile(" not in PAGE_HTML
    assert "onclick=\"deleteFile(" not in PAGE_HTML
    assert 'data-path="\'+_esc(f.path)+\'"' in PAGE_HTML
    assert "addEventListener" in PAGE_HTML


# ── Memory sessions (loadMemorySessions) ──────────────


def test_memory_sessions_no_inline_onclick_with_html_escaper():
    # Old: onclick="loadMemoryTimeline(\''+_esc(s.id)+'\')" — HTML escaper
    # used in a JS-string-in-attribute context; &#39; decodes back to a quote
    # before the JS parser runs, so it does not neutralize breakouts.
    assert "onclick=\"loadMemoryTimeline(" not in PAGE_HTML
    assert 'data-sid="\'+_esc(s.id)+\'"' in PAGE_HTML


# ── Chart helpers (audit findings) ────────────────────


def test_hbar_chart_escapes_labels():
    # Labels come from f.path via loadOverviewPanels.
    assert "title=\"'+item.label+'\"" not in PAGE_HTML
    assert "_esc(item.label)" in PAGE_HTML


def test_vbar_chart_escapes_labels():
    # Labels come from s.project / s.id via loadOverviewPanels.
    assert ">'+item.label+'</div>" not in PAGE_HTML


def test_memory_timeline_escapes_prompt_number():
    assert "'+t.prompt_number+'" not in PAGE_HTML
    assert "_esc(t.prompt_number)" in PAGE_HTML
