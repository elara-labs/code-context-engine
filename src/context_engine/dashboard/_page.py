"""Embedded HTML page for the CCE dashboard.

Single-file SPA. Fetches data from /api/* on tab switch.
Polls /api/status every 5 seconds for live updates.
No external dependencies — all CSS and JS inline.
Grafana-inspired dark theme with SVG/CSS charts.
"""

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CCE Dashboard</title>
<style>
:root {
  --bg:        #111217;
  --bg2:       #0d0f13;
  --panel:     #181b1f;
  --panel2:    #1e2128;
  --panel3:    #23272e;
  --border:    #2d3035;
  --border2:   #3a3f47;
  --text:      #d8dce2;
  --text2:     #8e97a5;
  --text3:     #555d6b;
  --blue:      #5794f2;
  --blue-bg:   rgba(87,148,242,.1);
  --green:     #3ecf8e;
  --green-bg:  rgba(62,207,142,.1);
  --yellow:    #f2cc0c;
  --yellow-bg: rgba(242,204,12,.1);
  --red:       #f15f5f;
  --red-bg:    rgba(241,95,95,.1);
  --orange:    #ff9830;
  --orange-bg: rgba(255,152,48,.1);
  --purple:    #b877d9;
  --purple-bg: rgba(184,119,217,.1);
  --mono: "DM Mono","JetBrains Mono","Fira Code","Cascadia Code","SF Mono",monospace;
  --sans: "DM Sans",Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --r: 6px;
  --r2: 10px;
  --shadow: 0 2px 8px rgba(0,0,0,.3), 0 1px 3px rgba(0,0,0,.2);
  --shadow-hover: 0 8px 24px rgba(0,0,0,.4), 0 2px 8px rgba(0,0,0,.3);
  --shadow-glow-blue: 0 0 20px rgba(87,148,242,.15);
  --shadow-glow-green: 0 0 20px rgba(115,191,105,.15);
  --shadow-glow-purple: 0 0 20px rgba(184,119,217,.15);
  --ease: cubic-bezier(0.22,1,0.36,1);
}

@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; }
body { background: var(--bg2); color: var(--text); font-family: var(--sans); font-size: 13px; display: flex; flex-direction: column; }

/* ── Top bar ─────────────────────────────────────── */

.topbar {
  height: 41px; min-height: 41px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center;
  padding: 0 16px; gap: 0; z-index: 10;
}
.topbar-logo {
  display: flex; align-items: center; gap: 8px;
  padding-right: 16px;
  border-right: 1px solid var(--border);
  margin-right: 16px; flex-shrink: 0;
}
.logo-icon {
  width: 24px; height: 24px;
  background: linear-gradient(135deg, var(--blue), var(--purple));
  border-radius: var(--r);
  display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 900; color: #fff;
  letter-spacing: -.5px; font-family: var(--mono);
  box-shadow: 0 2px 8px rgba(87,148,242,.3), 0 0 12px rgba(184,119,217,.2);
}
.topbar-title { font-size: 13.5px; font-weight: 600; color: var(--text); letter-spacing: .1px; }
.breadcrumb { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text2); }
.breadcrumb-sep { color: var(--text3); }
.breadcrumb-project {
  font-family: var(--mono); font-size: 11.5px;
  color: var(--blue); background: var(--blue-bg);
  padding: 2px 8px; border-radius: var(--r);
  border: 1px solid rgba(87,148,242,.2);
}
.topbar-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.live-badge {
  display: flex; align-items: center; gap: 5px;
  font-size: 11px; color: var(--text3); font-family: var(--mono);
}
.live-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 0 0 rgba(115,191,105,.5);
  animation: livepulse 2s ease-in-out infinite;
}
@keyframes livepulse {
  0%   { box-shadow: 0 0 0 0 rgba(62,207,142,.5); }
  70%  { box-shadow: 0 0 0 6px rgba(62,207,142,0); }
  100% { box-shadow: 0 0 0 0 rgba(62,207,142,0); }
}

/* ── Layout ──────────────────────────────────────── */
.layout { display: flex; flex: 1; overflow: hidden; }

/* ── Sidebar ─────────────────────────────────────── */
.sidebar {
  width: 200px; min-width: 200px;
  background: var(--panel); border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  overflow-y: auto; overflow-x: hidden;
}
.nav-section-label {
  padding: 14px 14px 5px;
  font-size: 10px; font-weight: 700;
  letter-spacing: 1px; text-transform: uppercase;
  color: var(--text3);
}
.nav-item {
  display: flex; align-items: center; gap: 9px;
  padding: 8px 14px;
  color: var(--text2); font-size: 13px;
  cursor: pointer; border: none; background: none;
  width: 100%; text-align: left;
  transition: background .1s, color .1s;
  border-left: 3px solid transparent;
}
.nav-item svg { flex-shrink: 0; opacity: .6; }
.nav-item:hover { background: var(--panel2); color: var(--text); }
.nav-item:hover svg { opacity: .85; }
.nav-item.active { background: var(--panel2); color: var(--text); border-left-color: var(--blue); font-weight: 500; }
.nav-item.active svg { opacity: 1; color: var(--blue); }
.nav-count {
  margin-left: auto; font-size: 10.5px;
  font-family: var(--mono); color: var(--text3);
  background: var(--panel3); padding: 1px 6px;
  border-radius: 10px; min-width: 22px; text-align: center;
}
.nav-item.active .nav-count { color: var(--blue); background: var(--blue-bg); }
.sidebar-spacer { flex: 1; }

/* ── Main ────────────────────────────────────────── */
.main { flex: 1; overflow-y: auto; background: var(--bg); }
.page { display: none; padding: 20px 24px; }
.page.active { display: block; }

/* ── Page header ─────────────────────────────────── */
.page-hdr {
  display: flex; align-items: flex-start;
  justify-content: space-between; margin-bottom: 18px;
}
.page-hdr-title { font-size: 17px; font-weight: 700; color: var(--text); letter-spacing: -.2px; }
.page-hdr-sub   { font-size: 12px; color: var(--text2); margin-top: 2px; }

/* ── Stat row ────────────────────────────────────── */
.stat-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px; margin-bottom: 14px;
}
.stat-card {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--r2); padding: 14px 16px;
  border-top: 2px solid transparent;
  display: flex; align-items: flex-start; justify-content: space-between;
  box-shadow: var(--shadow);
  transition: transform .25s var(--ease), box-shadow .25s var(--ease);
}
.stat-card:hover {
  transform: translateY(-2px) scale(1.02);
  box-shadow: var(--shadow-hover);
}
.stat-card.blue:hover   { box-shadow: var(--shadow-hover), var(--shadow-glow-blue); }
.stat-card.green:hover  { box-shadow: var(--shadow-hover), var(--shadow-glow-green); }
.stat-card.purple:hover { box-shadow: var(--shadow-hover), var(--shadow-glow-purple); }
.stat-card.blue   { border-top-color: var(--blue);   }
.stat-card.green  { border-top-color: var(--green);  }
.stat-card.yellow { border-top-color: var(--yellow); }
.stat-card.purple { border-top-color: var(--purple); }
.stat-left { flex: 1; }
.stat-label {
  font-size: 11px; font-weight: 600;
  letter-spacing: .6px; text-transform: uppercase;
  color: var(--text2); margin-bottom: 8px;
}
.stat-num {
  font-size: 28px; font-weight: 800;
  font-family: var(--mono);
  letter-spacing: -1px; line-height: 1; color: var(--text);
}
.stat-num.blue   { color: var(--blue);   }
.stat-num.green  { color: var(--green);  }
.stat-num.yellow { color: var(--yellow); }
.stat-num.purple { color: var(--purple); }
/* mini sparkline in stat card */
.stat-spark {
  display: flex; align-items: flex-end;
  gap: 2px; height: 32px; margin-left: 10px; flex-shrink: 0;
}
.spark-bar {
  width: 4px; border-radius: 2px 2px 0 0; min-height: 3px;
  transition: height .4s;
}

/* ── Panel grid ─────────────────────────────────── */
.panel-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
.panel {
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--r2); overflow: hidden;
  box-shadow: var(--shadow);
  transition: transform .3s var(--ease), box-shadow .3s var(--ease), border-color .3s;
}
.panel:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-hover);
  border-color: var(--border2);
}
.panel-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--panel2);
}
.panel-title {
  font-size: 11px; font-weight: 700;
  letter-spacing: .6px; text-transform: uppercase;
  color: var(--text2); display: flex; align-items: center; gap: 6px;
}
.panel-title svg { opacity: .5; }
.panel-body { padding: 12px 14px; }

/* ── Charts ──────────────────────────────────────── */

/* Donut */
.donut-wrap {
  display: flex; align-items: center; gap: 18px;
  padding: 16px 16px 14px;
}
.donut-svg { flex-shrink: 0; }
.donut-center-big {
  font-family: var(--mono); font-size: 20px; font-weight: 800;
  dominant-baseline: auto;
}
.donut-center-sub {
  font-family: var(--sans); font-size: 10px;
  fill: var(--text3); dominant-baseline: auto;
}
.donut-legend { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.legend-item { display: flex; align-items: center; gap: 8px; }
.legend-color { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.legend-label { font-size: 12px; color: var(--text2); flex: 1; }
.legend-val { font-size: 12px; font-family: var(--mono); font-weight: 600; color: var(--text); }
.legend-pct { font-size: 11px; font-family: var(--mono); color: var(--text3); margin-left: 4px; }

/* Horizontal bar chart */
.hbar-chart { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.hbar-row { display: flex; align-items: center; gap: 8px; }
.hbar-label {
  font-size: 11px; font-family: var(--mono); color: var(--text2);
  width: 120px; min-width: 120px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.hbar-track { flex: 1; height: 8px; background: var(--panel3); border-radius: 4px; overflow: hidden; }
.hbar-fill { height: 100%; border-radius: 4px; transition: width .8s cubic-bezier(.4,0,.2,1); }
.hbar-num { font-size: 11px; font-family: var(--mono); color: var(--text3); width: 32px; text-align: right; flex-shrink: 0; }

/* Vertical bar chart */
.vbar-chart {
  display: flex; align-items: flex-end; gap: 5px;
  height: 90px; padding: 12px 14px 0;
}
.vbar-col {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; gap: 4px; height: 100%; justify-content: flex-end;
}
.vbar-fill { width: 100%; border-radius: 3px 3px 0 0; min-height: 3px; }
.vbar-labels {
  display: flex; gap: 5px; padding: 4px 14px 12px;
}
.vbar-lbl {
  flex: 1; font-size: 8px; font-family: var(--mono);
  color: var(--text3); text-align: center;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* Stacked bar */
.stacked-bar {
  height: 18px; border-radius: 4px; overflow: hidden;
  display: flex; background: var(--panel3); margin: 14px 14px 8px;
}
.stacked-seg { height: 100%; transition: width .8s cubic-bezier(.4,0,.2,1); }
.stacked-labels { display: flex; justify-content: space-between; padding: 0 14px 12px; }
.stacked-lbl { font-size: 11px; font-family: var(--mono); }

/* ── Savings stat row ────────────────────────────── */
.savings-stat-row {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 10px; margin-bottom: 14px;
}

/* ── Health rows ─────────────────────────────────── */
.health-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 0; border-bottom: 1px solid var(--border);
}
.health-row:last-child { border-bottom: none; }
.health-left { display: flex; align-items: center; gap: 9px; font-size: 13px; color: var(--text); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.status-dot.ok      { background: var(--green);  box-shadow: 0 0 8px rgba(62,207,142,.5); }
.status-dot.stale   { background: var(--yellow); box-shadow: 0 0 6px rgba(242,204,12,.4); }
.status-dot.missing { background: var(--red);    box-shadow: 0 0 6px rgba(241,95,95,.4); }

/* ── Badges ──────────────────────────────────────── */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: var(--r);
  font-size: 11px; font-weight: 600; font-family: var(--mono);
  border: 1px solid transparent;
}
.badge::before { content: ''; width: 5px; height: 5px; border-radius: 50%; }
.badge-ok      { background: var(--green-bg);  color: var(--green);  border-color: rgba(115,191,105,.2); }
.badge-ok::before { background: var(--green); }
.badge-stale   { background: var(--yellow-bg); color: var(--yellow); border-color: rgba(242,204,12,.2); }
.badge-stale::before { background: var(--yellow); }
.badge-missing { background: var(--red-bg);    color: var(--red);    border-color: rgba(241,95,95,.2); }
.badge-missing::before { background: var(--red); }
.badge-active  { background: var(--blue-bg);   color: var(--blue);   border-color: rgba(87,148,242,.2); }
.badge-active::before { background: var(--blue); }
.badge-closed  { background: var(--panel3);    color: var(--text2);  border-color: var(--border); }
.badge-closed::before { background: var(--text3); }
.badge-num     { background: var(--panel3);    color: var(--text2);  border-color: var(--border); font-family: var(--mono); }
.badge-num::before { display: none; }

/* ── Buttons ─────────────────────────────────────── */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: var(--r2);
  font-size: 12.5px; font-weight: 500; border: 1px solid transparent;
  cursor: pointer; transition: all .12s; font-family: var(--sans); white-space: nowrap;
}
.btn-primary { background: var(--blue); color: #fff; border-color: var(--blue); box-shadow: 0 2px 8px rgba(87,148,242,.3); }
.btn-primary:hover { background: #6aa3f5; box-shadow: 0 4px 16px rgba(87,148,242,.5); transform: translateY(-1px); }
.btn-ghost { background: transparent; color: var(--text2); border-color: var(--border2); }
.btn-ghost:hover { background: var(--panel2); color: var(--text); }
.btn-danger { background: transparent; color: var(--red); border-color: rgba(241,95,95,.3); }
.btn-danger:hover { background: var(--red-bg); border-color: rgba(241,95,95,.5); }
.btn-row {
  display: flex; gap: 8px; padding: 12px 14px;
  border-top: 1px solid var(--border); background: var(--panel2);
}
.btn-icon {
  width: 26px; height: 26px; display: inline-flex;
  align-items: center; justify-content: center;
  background: var(--panel2); border: 1px solid var(--border);
  border-radius: var(--r); color: var(--text2);
  cursor: pointer; transition: all .12s; padding: 0;
}
.btn-icon:hover { background: var(--panel3); color: var(--text); border-color: var(--border2); }
.btn-icon.del:hover { background: var(--red-bg); color: var(--red); border-color: rgba(241,95,95,.3); }

/* ── Toolbar ─────────────────────────────────────── */
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.search-wrap { position: relative; flex: 1; max-width: 260px; }
.search-wrap .ico {
  position: absolute; left: 9px; top: 50%;
  transform: translateY(-50%); color: var(--text3);
  pointer-events: none; display: flex;
}
.search-input {
  width: 100%; background: var(--panel); border: 1px solid var(--border);
  color: var(--text); padding: 7px 10px 7px 30px;
  border-radius: var(--r2); font-size: 12.5px; font-family: var(--sans);
  outline: none; transition: border-color .15s;
}
.search-input:focus { border-color: var(--blue); }
.search-input::placeholder { color: var(--text3); }

/* ── Data table ──────────────────────────────────── */
.data-table { background: var(--panel); border: 1px solid var(--border); border-radius: var(--r2); overflow: hidden; box-shadow: var(--shadow); }
.table-head {
  display: grid;
  grid-template-columns: minmax(0,1fr) 80px 110px 72px;
  padding: 8px 14px; background: var(--panel2);
  border-bottom: 1px solid var(--border);
  font-size: 10.5px; font-weight: 700;
  letter-spacing: .7px; text-transform: uppercase; color: var(--text3); gap: 12px;
}
.table-row {
  display: grid;
  grid-template-columns: minmax(0,1fr) 80px 110px 72px;
  padding: 9px 14px; border-top: 1px solid var(--border);
  align-items: center; gap: 12px; transition: background .1s;
}
.table-row:nth-child(even) { background: rgba(255,255,255,.012); }
.table-row:hover { background: var(--panel2); }
.file-path { font-family: var(--mono); font-size: 11.5px; color: var(--blue); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chunk-num { font-family: var(--mono); font-size: 12px; color: var(--text2); }
.row-acts { display: flex; gap: 4px; align-items: center; }

/* ── Empty state ─────────────────────────────────── */
.empty {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; padding: 40px 20px;
  gap: 8px; color: var(--text3);
}
.empty-icon { opacity: .25; }
.empty-title { font-size: 13px; font-weight: 600; color: var(--text2); }
.empty-hint  { font-size: 11.5px; color: var(--text3); }

/* ── Sessions ────────────────────────────────────── */
.session-list { display: flex; flex-direction: column; gap: 8px; }
.session-card {
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--r2); overflow: hidden;
  box-shadow: var(--shadow);
  transition: border-color .25s var(--ease), box-shadow .25s var(--ease), transform .25s var(--ease);
}
.session-card:hover { border-color: var(--border2); box-shadow: var(--shadow-hover); transform: translateY(-1px); }
.session-header { display: flex; align-items: center; padding: 11px 14px; cursor: pointer; gap: 10px; }
.session-header:hover { background: var(--panel2); }
.session-info { flex: 1; min-width: 0; }
.session-name { font-size: 13px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-meta { font-size: 11.5px; color: var(--text2); margin-top: 2px; font-family: var(--mono); display: flex; gap: 8px; flex-wrap: wrap; }
.session-meta span { color: var(--text3); }
.session-meta b { color: var(--text2); font-weight: 400; }
.chevron { color: var(--text3); transition: transform .2s; flex-shrink: 0; }
.chevron.open { transform: rotate(90deg); }
.session-body { display: none; border-top: 1px solid var(--border); padding: 12px 14px; background: var(--bg2); }
.session-body.open { display: block; }
.decisions-label { font-size: 10px; font-weight: 700; letter-spacing: .8px; text-transform: uppercase; color: var(--text3); margin-bottom: 8px; }
.decision-item {
  background: var(--panel); border: 1px solid var(--border);
  border-left: 3px solid var(--blue); border-radius: var(--r);
  padding: 7px 11px; font-size: 12.5px; color: var(--text);
  margin-bottom: 5px; line-height: 1.55;
}

/* ── Savings ─────────────────────────────────────── */
.savings-summary {
  display: flex; align-items: center; justify-content: space-between;
  margin: 14px 14px 0; padding: 12px 14px;
  background: var(--green-bg); border: 1px solid rgba(62,207,142,.25);
  border-radius: var(--r2);
  box-shadow: 0 0 20px rgba(62,207,142,.08);
}
.savings-summary-lbl { font-size: 12px; color: var(--text2); }
.savings-summary-val { font-size: 20px; font-weight: 800; font-family: var(--mono); color: var(--green); letter-spacing: -1px; }
.savings-summary-pct { font-size: 12px; color: var(--green); opacity: .7; margin-left: 4px; }

/* Compression */
.comp-label { font-size: 12px; color: var(--text2); margin-bottom: 10px; line-height: 1.6; }
.comp-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 6px; }
.comp-btn {
  padding: 9px 4px; border-radius: var(--r);
  font-size: 11.5px; font-weight: 600; font-family: var(--mono);
  background: var(--panel2); border: 1px solid var(--border);
  color: var(--text2); cursor: pointer; text-align: center;
  transition: all .12s; letter-spacing: .3px;
}
.comp-btn:hover { border-color: var(--border2); color: var(--text); background: var(--panel3); }
.comp-btn.active { background: var(--blue-bg); border-color: rgba(87,148,242,.4); color: var(--blue); }

/* ── Banner ──────────────────────────────────────── */
.banner {
  display: flex; align-items: center; gap: 10px;
  background: var(--blue-bg); border: 1px solid rgba(87,148,242,.25);
  border-radius: var(--r2); padding: 10px 14px;
  font-size: 12.5px; color: #7eb8ff; margin-bottom: 16px;
}
.banner code { font-family: var(--mono); background: rgba(87,148,242,.15); padding: 1px 6px; border-radius: var(--r); font-size: 11.5px; }

/* ── Toast ───────────────────────────────────────── */
.toast {
  position: fixed; bottom: 20px; right: 20px;
  background: var(--panel2); border: 1px solid var(--border2);
  border-left: 3px solid var(--blue); border-radius: var(--r2);
  padding: 10px 14px; font-size: 12.5px; color: var(--text);
  box-shadow: 0 4px 20px rgba(0,0,0,.5);
  opacity: 0; transform: translateX(12px);
  transition: opacity .18s, transform .18s;
  pointer-events: none; z-index: 200; max-width: 300px;
}
.toast.show { opacity: 1; transform: translateX(0); }

/* ── Spinner ─────────────────────────────────────── */
.spinner {
  display: inline-block; width: 12px; height: 12px;
  border: 1.5px solid var(--border2); border-top-color: var(--blue);
  border-radius: 50%; animation: spin .6s linear infinite; vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Fade-in animation ───────────────────────────── */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
.fade-in {
  animation: fadeUp .5s var(--ease) both;
}
.stat-row .stat-card:nth-child(1) { animation-delay: 0ms; }
.stat-row .stat-card:nth-child(2) { animation-delay: 60ms; }
.stat-row .stat-card:nth-child(3) { animation-delay: 120ms; }
.stat-row .stat-card:nth-child(4) { animation-delay: 180ms; }
.panel-row { animation: fadeUp .6s var(--ease) both; }
.panel-row:nth-child(3) { animation-delay: 200ms; }
.panel-row:nth-child(4) { animation-delay: 320ms; }
.page.active .stat-card,
.page.active .panel-row,
.page.active .data-table,
.page.active .session-list,
.page.active .savings-stat-row { animation: fadeUp .5s var(--ease) both; }

/* ── Scrollbar ───────────────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--panel3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border2); }
</style>
</head>
<body>

<!-- Top bar -->
<div class="topbar">
  <div class="topbar-logo">
    <div class="logo-icon">CCE</div>
    <span class="topbar-title">Context Engine</span>
  </div>
  <div class="breadcrumb">
    <span>Dashboards</span>
    <span class="breadcrumb-sep">/</span>
    <span class="breadcrumb-project" id="nav-project">loading\u2026</span>
  </div>
  <div class="topbar-right">
    <div class="live-badge">
      <div class="live-dot"></div>
      LIVE&nbsp;&nbsp;5s
    </div>
  </div>
</div>

<!-- Layout -->
<div class="layout">

  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="nav-section-label">General</div>

    <button class="nav-item active" onclick="showPage('overview')">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
      Overview
    </button>

    <button class="nav-item" onclick="showPage('files')">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      Files
      <span class="nav-count" id="nav-files-count">\u2014</span>
    </button>

    <button class="nav-item" onclick="showPage('sessions')">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      Sessions
      <span class="nav-count" id="nav-sessions-count">\u2014</span>
    </button>

    <button class="nav-item" onclick="showPage('memory')">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9 1.65 1.65 0 0 0 4.27 7.18l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      Memory
    </button>

    <div class="nav-section-label" style="margin-top:4px">Analytics</div>

    <button class="nav-item" onclick="showPage('savings')">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
      Savings
    </button>

    <div class="sidebar-spacer"></div>
  </aside>

  <!-- Main content -->
  <main class="main">

    <!-- ═══════════════════ OVERVIEW ═══════════════════ -->
    <div class="page active" id="page-overview">
      <div class="page-hdr">
        <div class="page-hdr-left">
          <div class="page-hdr-title">Overview</div>
          <div class="page-hdr-sub">Index health, token metrics and session activity</div>
        </div>
      </div>

      <div id="uninit-banner" class="banner" style="display:none">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        Index not initialised \u2014 run <code>cce init</code> then <code>cce index</code> in your project directory.
      </div>

      <!-- Stat cards with inline sparklines -->
      <div class="stat-row">
        <div class="stat-card blue">
          <div class="stat-left">
            <div class="stat-label">Chunks indexed</div>
            <div class="stat-num blue" id="stat-chunks">\u2014</div>
          </div>
          <div class="stat-spark" id="spark-chunks"></div>
        </div>
        <div class="stat-card green">
          <div class="stat-left">
            <div class="stat-label">Files indexed</div>
            <div class="stat-num green" id="stat-files">\u2014</div>
          </div>
          <svg width="32" height="32" viewBox="0 0 32 32" id="spark-files-ring" style="flex-shrink:0;margin-left:8px"></svg>
        </div>
        <div class="stat-card yellow">
          <div class="stat-left">
            <div class="stat-label">Queries run</div>
            <div class="stat-num yellow" id="stat-queries">\u2014</div>
          </div>
          <div class="stat-spark" id="spark-queries"></div>
        </div>
        <div class="stat-card purple">
          <div class="stat-left">
            <div class="stat-label">Tokens saved</div>
            <div class="stat-num purple" id="stat-saved">\u2014</div>
          </div>
          <svg width="32" height="32" viewBox="0 0 32 32" id="spark-saved-ring" style="flex-shrink:0;margin-left:8px"></svg>
        </div>
      </div>

      <!-- Row 1: Token Savings Donut + File Health Donut -->
      <div class="panel-row">
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
              Token Savings
            </div>
          </div>
          <div id="chart-token-savings">
            <div class="empty"><div class="spinner"></div></div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
              File Health
            </div>
          </div>
          <div id="chart-file-health">
            <div class="empty"><div class="spinner"></div></div>
          </div>
        </div>
      </div>

      <!-- Row 2: Top Files Bar Chart + Session Activity -->
      <div class="panel-row">
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="4" height="18"/><rect x="10" y="8" width="4" height="13"/><rect x="18" y="13" width="4" height="8"/></svg>
              Top Files by Chunks
            </div>
          </div>
          <div id="chart-top-files">
            <div class="empty"><div class="spinner"></div></div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
              Session Decisions
            </div>
          </div>
          <div id="chart-sessions-bars">
            <div class="empty"><div class="spinner"></div></div>
          </div>
          <div class="btn-row">
            <button class="btn btn-primary" onclick="doReindex(false)" id="btn-reindex-changed">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
              Reindex changed
            </button>
            <button class="btn btn-ghost" onclick="doReindex(true)" id="btn-reindex-full">Full reindex</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══════════════════ FILES ═══════════════════ -->
    <div class="page" id="page-files">
      <div class="page-hdr">
        <div class="page-hdr-left">
          <div class="page-hdr-title">Files</div>
          <div class="page-hdr-sub">Indexed files with staleness status and chunk counts</div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-ghost" onclick="doExport()">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Export
          </button>
          <button class="btn btn-danger" onclick="doClear()">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
            Clear index
          </button>
        </div>
      </div>

      <!-- File status distribution bar -->
      <div id="files-dist-bar" style="margin-bottom:12px"></div>

      <div class="toolbar">
        <div class="search-wrap">
          <span class="ico">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          </span>
          <input class="search-input" placeholder="Filter by path\u2026" oninput="filterFiles(this.value)">
        </div>
      </div>
      <div class="data-table">
        <div class="table-head"><div>Path</div><div>Chunks</div><div>Status</div><div>Actions</div></div>
        <div id="file-rows"><div class="empty"><div class="spinner"></div></div></div>
      </div>
    </div>

    <!-- ═══════════════════ SESSIONS ═══════════════════ -->
    <div class="page" id="page-sessions">
      <div class="page-hdr">
        <div class="page-hdr-left">
          <div class="page-hdr-title">Sessions</div>
          <div class="page-hdr-sub">Captured Claude coding sessions and architectural decisions</div>
        </div>
      </div>
      <div id="session-list"><div class="empty"><div class="spinner"></div></div></div>
    </div>

    <!-- ═══════════════════ SAVINGS ═══════════════════ -->
    <div class="page" id="page-savings">
      <div class="page-hdr">
        <div class="page-hdr-left">
          <div class="page-hdr-title">Savings</div>
          <div class="page-hdr-sub">Token reduction metrics and output compression settings</div>
        </div>
      </div>

      <!-- Savings stat cards -->
      <div class="savings-stat-row">
        <div class="stat-card yellow">
          <div class="stat-left">
            <div class="stat-label">Queries processed</div>
            <div class="stat-num yellow" id="sv-queries">\u2014</div>
          </div>
        </div>
        <div class="stat-card green">
          <div class="stat-left">
            <div class="stat-label">Tokens saved</div>
            <div class="stat-num green" id="sv-saved">\u2014</div>
          </div>
        </div>
        <div class="stat-card purple">
          <div class="stat-left">
            <div class="stat-label">Savings rate</div>
            <div class="stat-num purple" id="sv-pct">\u2014</div>
          </div>
          <svg width="32" height="32" viewBox="0 0 32 32" id="sv-ring" style="flex-shrink:0;margin-left:8px"></svg>
        </div>
      </div>

      <!-- Chart row: big donut + stacked breakdown -->
      <div class="panel-row" style="margin-bottom:10px">
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
              Token Usage Breakdown
            </div>
          </div>
          <div id="sv-donut-chart">
            <div class="empty"><div class="spinner"></div></div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="4" rx="1"/><rect x="2" y="10" width="20" height="4" rx="1"/><rect x="2" y="17" width="20" height="4" rx="1"/></svg>
              Token Budget
            </div>
          </div>
          <div id="sv-budget-panel">
            <div class="empty"><div class="spinner"></div></div>
          </div>
        </div>
      </div>

      <!-- Compression panel -->
      <div class="panel">
        <div class="panel-head">
          <div class="panel-title">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M4.93 4.93a10 10 0 0 0 0 14.14"/></svg>
            Output Compression
          </div>
        </div>
        <div class="panel-body">
          <p class="comp-label">Controls how Claude compresses its responses. Higher levels reduce output token usage at the cost of verbosity.</p>
          <div class="comp-grid" id="comp-buttons">
            <button class="comp-btn" onclick="setCompression('off')">off</button>
            <button class="comp-btn" onclick="setCompression('lite')">lite</button>
            <button class="comp-btn" onclick="setCompression('standard')">standard</button>
            <button class="comp-btn" onclick="setCompression('max')">max</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══════════════════ MEMORY ═══════════════════ -->
    <div class="page" id="page-memory">
      <div class="page-hdr">
        <div class="page-hdr-left">
          <div class="page-hdr-title">Memory</div>
          <div class="page-hdr-sub">Cross-session memory store (memory.db) — sessions, timelines, decisions</div>
        </div>
        <div class="page-hdr-right">
          <div class="comp-grid" style="display:flex; gap:6px">
            <button class="comp-btn comp-btn-active" id="mem-tab-sessions" onclick="memShowTab('sessions')">Sessions</button>
            <button class="comp-btn" id="mem-tab-decisions" onclick="memShowTab('decisions')">Decisions</button>
          </div>
        </div>
      </div>

      <!-- Sessions list + drill-down view (kept side by side; the drill panel
           appears when a session is selected). -->
      <div class="memory-pane" id="mem-pane-sessions">
        <div class="data-table">
          <div class="table-head"><div>Session</div><div>Started</div><div>Status</div><div>Prompts</div><div>Rollup</div></div>
          <div id="mem-sessions-rows"></div>
        </div>

        <div class="panel" id="mem-timeline-panel" style="margin-top:14px; display:none">
          <div class="panel-head">
            <div class="panel-title" id="mem-timeline-title">Session timeline</div>
          </div>
          <div class="panel-body" id="mem-timeline-body"></div>
        </div>
      </div>

      <!-- Decisions search -->
      <div class="memory-pane" id="mem-pane-decisions" style="display:none">
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">Decisions search</div>
          </div>
          <div class="panel-body">
            <div style="display:flex; gap:8px; margin-bottom:10px">
              <input id="mem-decision-q" type="text" placeholder="search decisions (FTS5)…" style="flex:1; padding:6px 10px; background:var(--panel2); border:1px solid var(--border); color:var(--text); border-radius:4px">
              <select id="mem-decision-source" style="padding:6px 10px; background:var(--panel2); border:1px solid var(--border); color:var(--text); border-radius:4px">
                <option value="">all sources</option>
                <option value="manual">manual</option>
                <option value="auto">auto</option>
                <option value="migrated">migrated</option>
              </select>
              <button class="comp-btn" onclick="memDecisionSearch()">search</button>
            </div>
            <div class="data-table">
              <div class="table-head"><div>Decision</div><div>Reason</div><div>Source</div><div>When</div></div>
              <div id="mem-decision-rows"></div>
            </div>
          </div>
        </div>
      </div>
    </div>

  </main>
</div>

<div class="toast" id="toast"></div>

<script>
var API = '';
var allFiles = [];
var currentLevel = 'standard';
var PAGES = ['overview','files','sessions','memory','savings'];

// Pick up an optional bearer token from the URL (?token=...). The server
// only enforces it on mutating endpoints when CCE_DASHBOARD_TOKEN is set;
// when it's not set the token query param is harmless. Monkey-patches
// fetch() to attach the header on every request — sending it on GETs as
// well is harmless and keeps the rest of the code untouched.
(function() {
  var token = new URLSearchParams(window.location.search).get('token');
  if (!token) return;
  var origFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    init = init || {};
    var headers = new Headers(init.headers || (input && input.headers) || {});
    if (!headers.has('Authorization')) {
      headers.set('Authorization', 'Bearer ' + token);
    }
    init.headers = headers;
    return origFetch(input, init);
  };
})();

// ── Chart helpers ────────────────────────────────

/**
 * Draw SVG donut segments into an <svg> element.
 * segments: [{value, color}]
 * The SVG must be 132x132 with cx=66, cy=66, r=52.
 */
function drawDonutSVG(svgEl, segments) {
  var r = 52, cx = 66, cy = 66;
  var circ = 2 * Math.PI * r;
  var total = segments.reduce(function(a,s){ return a+(s.value||0); }, 0);
  // background track
  var html = '<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--panel3)" stroke-width="13"/>';
  var acc = 0;
  segments.forEach(function(seg) {
    if (!seg.value || !total) return;
    var dash = seg.value / total * circ;
    var gap  = circ - dash;
    // offset: start at top (12 o'clock) = circ/4, then advance by accumulated
    var offset = circ / 4 - acc;
    html += '<circle cx="'+cx+'" cy="'+cy+'" r="'+r
      +'" fill="none" stroke="'+seg.color+'" stroke-width="13"'
      +' stroke-dasharray="'+dash+' '+gap+'"'
      +' stroke-dashoffset="'+offset+'"/>';
    acc += dash;
  });
  svgEl.innerHTML = html;
}

/** Render a full donut panel: SVG + legend. */
function renderDonutPanel(containerId, segments, centerBig, centerSub, centerColor) {
  var el = document.getElementById(containerId);
  if (!el) return;
  var total = segments.reduce(function(a,s){ return a+(s.value||0); }, 0);
  var legendHtml = segments.map(function(s) {
    var pct = total > 0 ? Math.round(s.value/total*100) : 0;
    return '<div class="legend-item">'
      +'<div class="legend-color" style="background:'+s.color+'"></div>'
      +'<span class="legend-label">'+s.label+'</span>'
      +'<span class="legend-val">'+s.display+'</span>'
      +'<span class="legend-pct">'+pct+'%</span>'
      +'</div>';
  }).join('');
  el.innerHTML =
    '<div class="donut-wrap">'
    +'<svg class="donut-svg" id="'+containerId+'-svg" width="132" height="132" viewBox="0 0 132 132">'
    +'<text x="66" y="62" text-anchor="middle" font-family="monospace" font-size="19" font-weight="800" fill="'+(centerColor||'var(--text)')+'">'+centerBig+'</text>'
    +'<text x="66" y="78" text-anchor="middle" font-family="sans-serif" font-size="10" fill="var(--text3)">'+centerSub+'</text>'
    +'</svg>'
    +'<div class="donut-legend">'+legendHtml+'</div>'
    +'</div>';
  var svg = document.getElementById(containerId+'-svg');
  if (svg) drawDonutSVG(svg, segments);
}

/** Render mini ring in a 32x32 SVG (for stat cards). */
function drawMiniRing(svgId, pct, color) {
  var svg = document.getElementById(svgId);
  if (!svg) return;
  var r = 12, cx = 16, cy = 16;
  var circ = 2 * Math.PI * r;
  var dash = Math.min(pct/100, 1) * circ;
  svg.innerHTML =
    '<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--panel3)" stroke-width="4"/>'
    +'<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+color+'" stroke-width="4"'
    +' stroke-dasharray="'+dash+' '+(circ-dash)+'" stroke-dashoffset="'+(circ/4)+'" stroke-linecap="round"/>';
}

/** Render sparkline bars in a stat-spark container. */
function drawSparkline(containerId, values, color) {
  var el = document.getElementById(containerId);
  if (!el) return;
  var max = Math.max.apply(null, values);
  if (!max) { el.innerHTML = ''; return; }
  el.innerHTML = values.map(function(v, i) {
    var h = Math.max(10, Math.round(v/max*32));
    var opacity = 0.3 + (i / values.length) * 0.7;
    return '<div class="spark-bar" style="height:'+h+'px;background:'+color+';opacity:'+opacity+'"></div>';
  }).join('');
}

/** Render horizontal bar chart. items: [{label,value,color}] */
function renderHBarChart(containerId, items) {
  var el = document.getElementById(containerId);
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div class="empty"><span class="empty-title">No data yet</span></div>';
    return;
  }
  var max = Math.max.apply(null, items.map(function(i){ return i.value||0; }));
  el.innerHTML = '<div class="hbar-chart">'
    + items.map(function(item) {
      var pct = max > 0 ? item.value/max*100 : 0;
      var name = item.label.split('/').pop() || item.label;
      return '<div class="hbar-row">'
        +'<div class="hbar-label" title="'+item.label+'">'+name+'</div>'
        +'<div class="hbar-track"><div class="hbar-fill" style="width:'+pct+'%;background:'+item.color+'"></div></div>'
        +'<div class="hbar-num">'+item.value+'</div>'
        +'</div>';
    }).join('')
    +'</div>';
}

/** Render vertical bar chart. items: [{label,value}] */
function renderVBarChart(containerId, items, color) {
  var el = document.getElementById(containerId);
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div class="empty"><span class="empty-title">No sessions yet</span></div>';
    return;
  }
  var max = Math.max.apply(null, items.map(function(i){ return i.value||0; })) || 1;
  var bars = items.map(function(item, i) {
    var h = Math.max(4, Math.round(item.value/max*80));
    var opacity = 0.35 + (i / items.length) * 0.65;
    return '<div class="vbar-col">'
      +'<div class="vbar-fill" style="height:'+h+'px;background:'+color+';opacity:'+opacity+'"></div>'
      +'</div>';
  }).join('');
  var labels = items.map(function(item) {
    return '<div class="vbar-lbl" title="'+item.label+'">'+item.label+'</div>';
  }).join('');
  el.innerHTML =
    '<div class="vbar-chart">'+bars+'</div>'
    +'<div class="vbar-labels">'+labels+'</div>';
}

// ── Page routing ──────────────────────────────────

function showPage(name) {
  PAGES.forEach(function(p) {
    document.getElementById('page-'+p).classList.toggle('active', p===name);
  });
  document.querySelectorAll('.nav-item').forEach(function(el, i) {
    el.classList.toggle('active', PAGES[i]===name);
  });
  if (name==='files')    loadFiles();
  if (name==='sessions') loadSessions();
  if (name==='memory')   loadMemorySessions();
  if (name==='savings')  loadSavings();
}

// ── Memory page (PR 5) ───────────────────────────

function memShowTab(tab) {
  document.getElementById('mem-pane-sessions').style.display  = tab==='sessions'  ? '' : 'none';
  document.getElementById('mem-pane-decisions').style.display = tab==='decisions' ? '' : 'none';
  document.getElementById('mem-tab-sessions').classList.toggle('comp-btn-active', tab==='sessions');
  document.getElementById('mem-tab-decisions').classList.toggle('comp-btn-active', tab==='decisions');
  if (tab==='decisions') memDecisionSearch();
}

function _esc(s) {
  return String(s||'').replace(/[&<>"']/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

async function loadMemorySessions() {
  try {
    var r = await fetch(API+'/api/memory/sessions');
    var rows = await r.json();
    var box = document.getElementById('mem-sessions-rows');
    if (!rows.length) {
      box.innerHTML = '<div class="empty">No sessions captured yet. Start a Claude Code session in this project — hooks will populate the timeline.</div>';
      return;
    }
    box.innerHTML = rows.map(function(s) {
      var rollup = (s.rollup_summary || '').slice(0, 80);
      return ''
        + '<div class="table-row" onclick="loadMemoryTimeline(\''+_esc(s.id)+'\')" style="cursor:pointer">'
        +   '<div><code>'+_esc(s.id)+'</code></div>'
        +   '<div>'+_esc(s.started_at||'')+'</div>'
        +   '<div>'+_esc(s.status||'')+'</div>'
        +   '<div>'+_esc(s.prompt_count||0)+'</div>'
        +   '<div>'+_esc(rollup)+(rollup && s.rollup_summary && s.rollup_summary.length>80?'…':'')+'</div>'
        + '</div>';
    }).join('');
  } catch(e) {
    document.getElementById('mem-sessions-rows').innerHTML =
      '<div class="empty">Memory store unavailable.</div>';
  }
}

async function loadMemoryTimeline(sessionId) {
  try {
    var r = await fetch(API+'/api/memory/sessions/'+encodeURIComponent(sessionId)+'/timeline');
    var data = await r.json();
    var panel = document.getElementById('mem-timeline-panel');
    var title = document.getElementById('mem-timeline-title');
    var body  = document.getElementById('mem-timeline-body');
    panel.style.display = '';
    title.textContent = 'Session ' + sessionId;
    if (!data.session || !data.turns.length) {
      body.innerHTML = '<div class="empty">No turn summaries yet for this session.</div>';
      return;
    }
    var rollup = data.session.rollup_summary
      ? '<div style="margin-bottom:10px; padding:8px; background:var(--panel2); border-radius:4px"><strong>rollup:</strong> '+_esc(data.session.rollup_summary)+'</div>'
      : '';
    var turns = data.turns.map(function(t) {
      return ''
        + '<div style="padding:6px 0; border-bottom:1px solid var(--border)">'
        +   '<div style="font-size:11px; color:var(--muted)">turn '+t.prompt_number+' · ['+_esc(t.tier)+']</div>'
        +   '<div>'+_esc(t.summary)+'</div>'
        + '</div>';
    }).join('');
    body.innerHTML = rollup + turns;
  } catch(e) {
    document.getElementById('mem-timeline-body').innerHTML =
      '<div class="empty">Failed to load timeline.</div>';
  }
}

async function memDecisionSearch() {
  var q = (document.getElementById('mem-decision-q').value || '').trim();
  var src = document.getElementById('mem-decision-source').value || '';
  var url = API+'/api/memory/decisions';
  var params = [];
  if (q) params.push('q='+encodeURIComponent(q));
  if (src) params.push('source='+encodeURIComponent(src));
  if (params.length) url += '?' + params.join('&');
  try {
    var r = await fetch(url);
    var rows = await r.json();
    var box = document.getElementById('mem-decision-rows');
    if (!rows.length) {
      box.innerHTML = '<div class="empty">No decisions match.</div>';
      return;
    }
    box.innerHTML = rows.map(function(d) {
      return ''
        + '<div class="table-row">'
        +   '<div>'+_esc(d.decision)+'</div>'
        +   '<div>'+_esc(d.reason)+'</div>'
        +   '<div><span class="tag tag-'+_esc(d.source)+'">'+_esc(d.source)+'</span></div>'
        +   '<div>'+_esc(d.created_at||'')+'</div>'
        + '</div>';
    }).join('');
  } catch(e) {
    document.getElementById('mem-decision-rows').innerHTML =
      '<div class="empty">Decisions search failed.</div>';
  }
}

function toast(msg) {
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(function(){ el.classList.remove('show'); }, 3000);
}

function reltime(ts) {
  var d = Math.floor(Date.now()/1000 - ts);
  if (d < 60)    return 'just now';
  if (d < 3600)  return Math.floor(d/60)+'m ago';
  if (d < 86400) return Math.floor(d/3600)+'h ago';
  return Math.floor(d/86400)+'d ago';
}

function fmt(n) { return Number(n).toLocaleString(); }
function fmtK(n) { return n>=1000 ? (n/1000).toFixed(1)+'k' : String(n); }

var SVG = {
  refresh: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
  trash:   '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
  chevron: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
};

// ── Data loaders ──────────────────────────────────

async function loadStatus() {
  try {
    var r = await fetch(API+'/api/status');
    var d = await r.json();
    document.getElementById('nav-project').textContent  = d.project||'';
    document.getElementById('stat-chunks').textContent  = fmt(d.chunks);
    document.getElementById('stat-files').textContent   = fmt(d.files);
    document.getElementById('stat-queries').textContent = fmt(d.queries);
    document.getElementById('stat-saved').textContent   = (d.tokens_saved_pct||0)+'%';
    document.getElementById('uninit-banner').style.display = d.initialized?'none':'flex';
    currentLevel = d.output_level;
    refreshCompButtons(d.output_level);

    // Mini rings in stat cards
    drawMiniRing('spark-saved-ring', d.tokens_saved_pct||0, 'var(--purple)');

    loadOverviewPanels();
  } catch(e) {}
}

async function loadOverviewPanels() {
  // Load files + sessions in parallel for the chart panels
  try {
    var [rFiles, rSessions, rSavings] = await Promise.all([
      fetch(API+'/api/files'),
      fetch(API+'/api/sessions'),
      fetch(API+'/api/savings'),
    ]);
    var files    = await rFiles.json();
    var sessions = await rSessions.json();
    var savings  = await rSavings.json();

    // Update nav counts
    document.getElementById('nav-files-count').textContent    = files.length;
    document.getElementById('nav-sessions-count').textContent = sessions.length;

    // ── Token Savings Donut ──
    var served   = savings.served_tokens || 0;
    var saved    = savings.tokens_saved  || 0;
    var baseline = savings.baseline_tokens || 0;
    var savePct  = savings.savings_pct || 0;

    if (baseline > 0) {
      renderDonutPanel('chart-token-savings',
        [
          {value: saved,  color: 'var(--green)',  label: 'Tokens saved', display: fmtK(saved)},
          {value: served, color: 'var(--blue)',   label: 'Tokens used',  display: fmtK(served)},
        ],
        savePct+'%', 'saved', 'var(--green)'
      );
    } else {
      document.getElementById('chart-token-savings').innerHTML =
        '<div class="empty"><span class="empty-title">No queries yet</span><span class="empty-hint">Run context_search via MCP to start tracking</span></div>';
    }

    // ── File Health Donut ──
    var ok      = files.filter(function(f){ return f.status==='ok'; }).length;
    var stale   = files.filter(function(f){ return f.status==='stale'; }).length;
    var missing = files.filter(function(f){ return f.status==='missing'; }).length;
    var total   = files.length;

    // Mini ring: ok% in stat card
    drawMiniRing('spark-files-ring', total>0?Math.round(ok/total*100):0, 'var(--green)');

    if (total > 0) {
      renderDonutPanel('chart-file-health',
        [
          {value: ok,      color: 'var(--green)',  label: 'Up to date', display: String(ok)},
          {value: stale,   color: 'var(--yellow)', label: 'Stale',      display: String(stale)},
          {value: missing, color: 'var(--red)',     label: 'Missing',    display: String(missing)},
        ],
        total, 'files', 'var(--text)'
      );
    } else {
      document.getElementById('chart-file-health').innerHTML =
        '<div class="empty"><span class="empty-title">No files indexed</span></div>';
    }

    // ── Top Files Bar Chart ──
    var sorted = files.slice().sort(function(a,b){ return b.chunks - a.chunks; }).slice(0,10);
    var barColors = ['var(--blue)','var(--blue)','var(--blue)','var(--blue)','var(--blue)',
                     'var(--purple)','var(--purple)','var(--purple)','var(--purple)','var(--purple)'];
    renderHBarChart('chart-top-files',
      sorted.map(function(f, i){
        return {label: f.path, value: f.chunks, color: barColors[i]||'var(--blue)'};
      })
    );

    // ── Session Decisions Bar Chart ──
    var sessionItems = sessions.slice(0,12).map(function(s) {
      var label = s.project ? s.project.slice(0,8) : s.id.slice(0,6);
      return {label: label, value: (s.decisions||[]).length};
    });
    // Sparkline for queries (fake trend from session count)
    drawSparkline('spark-queries',
      sessions.slice(-5).map(function(s){ return (s.decisions||[]).length || 1; }),
      'var(--yellow)'
    );
    drawSparkline('spark-chunks', [2,4,3,5,6,8,7], 'var(--blue)');

    if (sessionItems.length) {
      renderVBarChart('chart-sessions-bars', sessionItems, 'var(--purple)');
    } else {
      document.getElementById('chart-sessions-bars').innerHTML =
        '<div class="empty"><span class="empty-title">No sessions yet</span></div>';
    }

  } catch(e) {}
}

// ── Files page ────────────────────────────────────

async function loadFiles() {
  var el = document.getElementById('file-rows');
  el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  try {
    var r = await fetch(API+'/api/files');
    allFiles = await r.json();
    renderFilesDistBar(allFiles);
    renderFiles(allFiles);
  } catch(e) {
    el.innerHTML = '<div class="empty"><span class="empty-title">Failed to load</span></div>';
  }
}

function renderFilesDistBar(files) {
  var el = document.getElementById('files-dist-bar');
  if (!el || !files.length) { if(el) el.innerHTML=''; return; }
  var ok      = files.filter(function(f){ return f.status==='ok'; }).length;
  var stale   = files.filter(function(f){ return f.status==='stale'; }).length;
  var missing = files.filter(function(f){ return f.status==='missing'; }).length;
  var total   = files.length;
  var pOk  = Math.round(ok/total*100);
  var pSt  = Math.round(stale/total*100);
  var pMi  = 100 - pOk - pSt;
  el.innerHTML =
    '<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--r2);overflow:hidden">'
    +'<div style="height:6px;display:flex">'
    +'<div style="width:'+pOk+'%;background:var(--green);transition:width .6s"></div>'
    +'<div style="width:'+pSt+'%;background:var(--yellow);transition:width .6s"></div>'
    +'<div style="flex:1;background:var(--red);transition:width .6s"></div>'
    +'</div>'
    +'<div style="display:flex;gap:16px;padding:8px 12px;font-size:11px;font-family:var(--mono)">'
    +'<span style="color:var(--green)">&#9679; ok &nbsp;'+ok+'</span>'
    +'<span style="color:var(--yellow)">&#9679; stale &nbsp;'+stale+'</span>'
    +'<span style="color:var(--red)">&#9679; missing &nbsp;'+missing+'</span>'
    +'<span style="margin-left:auto;color:var(--text3)">'+total+' files total</span>'
    +'</div>'
    +'</div>';
}

function renderFiles(files) {
  var el = document.getElementById('file-rows');
  if (!files.length) {
    el.innerHTML = '<div class="empty">'
      +'<svg class="empty-icon" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
      +'<span class="empty-title">No files indexed</span>'
      +'<span class="empty-hint">Run <code style="font-family:var(--mono)">cce index</code> to index your project</span>'
      +'</div>';
    return;
  }
  el.innerHTML = files.map(function(f) {
    return '<div class="table-row">'
      +'<div class="file-path" title="'+f.path+'">'+f.path+'</div>'
      +'<div class="chunk-num">'+f.chunks+'</div>'
      +'<div><span class="badge badge-'+f.status+'">'+f.status+'</span></div>'
      +'<div class="row-acts">'
        +'<button class="btn-icon" title="Reindex" onclick="reindexFile('+JSON.stringify(f.path)+')">'+SVG.refresh+'</button>'
        +'<button class="btn-icon del" title="Remove" onclick="deleteFile('+JSON.stringify(f.path)+')">'+SVG.trash+'</button>'
      +'</div>'
      +'</div>';
  }).join('');
}

function filterFiles(q) {
  q = q.toLowerCase();
  renderFiles(q ? allFiles.filter(function(f){ return f.path.toLowerCase().includes(q); }) : allFiles);
}

// ── Sessions page ─────────────────────────────────

async function loadSessions() {
  var el = document.getElementById('session-list');
  el.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  try {
    var r = await fetch(API+'/api/sessions');
    var sessions = await r.json();
    if (!sessions.length) {
      el.innerHTML = '<div class="empty">'
        +'<svg class="empty-icon" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
        +'<span class="empty-title">No sessions recorded</span>'
        +'<span class="empty-hint">Sessions are captured during Claude coding sessions</span>'
        +'</div>';
      return;
    }
    el.innerHTML = '<div class="session-list">'+sessions.map(function(s,i) {
      var isActive = !s.ended_at;
      var decs  = s.decisions  || [];
      var areas = s.code_areas || [];
      return '<div class="session-card">'
        +'<div class="session-header" onclick="toggleSession('+i+')">'
          +'<div class="chevron" id="chev-'+i+'">'+SVG.chevron+'</div>'
          +'<div class="session-info">'
            +'<div class="session-name">'+(s.project||s.id)+'</div>'
            +'<div class="session-meta">'
              +'<b>'+decs.length+'</b><span>decisions</span>'
              +'<b>'+areas.length+'</b><span>code areas</span>'
              +(s.started_at?'<b>'+reltime(s.started_at)+'</b>':'')
            +'</div>'
          +'</div>'
          +'<span class="badge '+(isActive?'badge-active':'badge-closed')+'">'+(isActive?'active':'closed')+'</span>'
        +'</div>'
        +(decs.length
          ?'<div class="session-body" id="sb-'+i+'">'
            +'<div class="decisions-label">Decisions</div>'
            +decs.map(function(d){ return '<div class="decision-item">'+d.decision+'</div>'; }).join('')
            +'</div>'
          :'')
        +'</div>';
    }).join('')+'</div>';
  } catch(e) {
    el.innerHTML = '<div class="empty"><span class="empty-title">Failed to load</span></div>';
  }
}

function toggleSession(i) {
  var body = document.getElementById('sb-'+i);
  var chev = document.getElementById('chev-'+i);
  if (body) body.classList.toggle('open');
  if (chev) chev.classList.toggle('open');
}

// ── Savings page ──────────────────────────────────

async function loadSavings() {
  try {
    var r = await fetch(API+'/api/savings');
    var d = await r.json();

    var queries  = d.queries        || 0;
    var saved    = d.tokens_saved   || 0;
    var served   = d.served_tokens  || 0;
    var baseline = d.baseline_tokens || 0;
    var pct      = d.savings_pct    || 0;
    var usedPct  = baseline > 0 ? Math.round(served/baseline*100) : 0;

    // Stat cards
    document.getElementById('sv-queries').textContent = fmt(queries);
    document.getElementById('sv-saved').textContent   = fmtK(saved);
    document.getElementById('sv-pct').textContent     = pct+'%';
    drawMiniRing('sv-ring', pct, 'var(--purple)');

    // Big donut
    if (baseline > 0) {
      renderDonutPanel('sv-donut-chart',
        [
          {value: saved,  color: 'var(--green)', label: 'Tokens saved', display: fmtK(saved)},
          {value: served, color: 'var(--blue)',  label: 'Tokens used',  display: fmtK(served)},
        ],
        pct+'%', 'saved', 'var(--green)'
      );
    } else {
      document.getElementById('sv-donut-chart').innerHTML =
        '<div class="empty"><span class="empty-title">No usage recorded yet</span></div>';
    }

    // Budget panel: stacked bar + summary
    if (baseline > 0) {
      document.getElementById('sv-budget-panel').innerHTML =
        '<div style="padding:14px 14px 4px;font-size:11px;font-family:var(--mono);color:var(--text3)">Token distribution across '+fmt(queries)+' queries</div>'
        +'<div class="stacked-bar">'
          +'<div class="stacked-seg" style="width:'+usedPct+'%;background:var(--blue)"></div>'
          +'<div class="stacked-seg" style="flex:1;background:var(--green);opacity:.7"></div>'
        +'</div>'
        +'<div class="stacked-labels">'
          +'<span class="stacked-lbl" style="color:var(--blue)">'+fmtK(served)+' used ('+usedPct+'%)</span>'
          +'<span class="stacked-lbl" style="color:var(--green)">'+fmtK(saved)+' saved ('+pct+'%)</span>'
        +'</div>'
        +'<div class="savings-summary" style="margin:0 14px 14px">'
          +'<div>'
            +'<div class="savings-summary-lbl">Total tokens saved vs reading raw files</div>'
            +'<div style="font-size:11px;color:var(--text3);margin-top:2px;font-family:var(--mono)">'+fmt(baseline)+' tokens baseline</div>'
          +'</div>'
          +'<div>'
            +'<span class="savings-summary-val">'+fmtK(saved)+'</span>'
            +'<span class="savings-summary-pct">('+pct+'%)</span>'
          +'</div>'
        +'</div>';
    } else {
      document.getElementById('sv-budget-panel').innerHTML =
        '<div class="empty"><span class="empty-title">No usage recorded yet</span><span class="empty-hint">Run context_search via MCP to start tracking</span></div>';
    }
  } catch(e) {}
  refreshCompButtons(currentLevel);
}

function refreshCompButtons(level) {
  document.querySelectorAll('.comp-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.textContent.trim()===level);
  });
}

// ── Actions ───────────────────────────────────────

async function doReindex(full) {
  var id  = full ? 'btn-reindex-full' : 'btn-reindex-changed';
  var btn = document.getElementById(id);
  var orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Indexing\u2026';
  try {
    var r = await fetch(API+'/api/reindex', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({full: full})
    });
    var d = await r.json();
    if (d.errors && d.errors.length) toast('Error: '+d.errors[0]);
    else toast('Indexed '+d.indexed_files.length+' files \u2014 '+fmt(d.total_chunks)+' chunks');
    loadStatus();
  } catch(e) { toast('Reindex failed'); }
  finally { btn.disabled=false; btn.innerHTML=orig; }
}

async function reindexFile(path) {
  try {
    await fetch(API+'/api/reindex/'+encodeURIComponent(path), {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    toast('Reindexed '+path);
    loadFiles(); loadStatus();
  } catch(e) { toast('Failed'); }
}

async function deleteFile(path) {
  if (!confirm('Remove "'+path+'" from index?')) return;
  try {
    await fetch(API+'/api/files/'+encodeURIComponent(path), {method:'DELETE'});
    toast('Removed '+path);
    loadFiles(); loadStatus();
  } catch(e) { toast('Failed'); }
}

async function doClear() {
  if (!confirm('Clear entire index? This cannot be undone.')) return;
  try {
    await fetch(API+'/api/clear', {method:'POST'});
    toast('Index cleared');
    loadStatus(); loadFiles();
  } catch(e) { toast('Failed'); }
}

async function doExport() { window.location.href = API+'/api/export'; }

async function setCompression(level) {
  try {
    await fetch(API+'/api/compression', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({level: level})
    });
    currentLevel = level;
    refreshCompButtons(level);
    toast('Compression: '+level);
  } catch(e) { toast('Failed'); }
}

// ── Boot ──────────────────────────────────────────
loadStatus();
setInterval(loadStatus, 5000);
</script>
</body>
</html>"""
