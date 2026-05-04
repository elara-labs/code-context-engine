# Operations

How to run CCE reliably as a long-lived process. None of this is needed for
ad-hoc developer use — `cce serve` from a terminal is fine for that. This page
is for "I want CCE up across reboots / I'm putting it on a shared host."

## Crash recovery model

The MCP server is one Python process. When Claude Code is the parent (the
default install path), Claude Code restarts it on demand and you don't need a
supervisor.

When you run CCE as a standalone daemon (e.g. `cce serve --http`, or `cce
dashboard` left running), nothing automatically restarts it on crash. Use a
service manager.

## systemd (Linux)

Drop this at `~/.config/systemd/user/cce-dashboard.service`:

```ini
[Unit]
Description=Code Context Engine — Dashboard
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/cce dashboard --no-browser --port 8080
Restart=on-failure
RestartSec=5s
# Optional: bearer-token-protect the dashboard. Generate once with
# `python -c "import secrets; print(secrets.token_urlsafe(32))"` and store
# the value in ~/.config/systemd/user/cce.env (mode 0600).
EnvironmentFile=-%h/.config/systemd/user/cce.env

[Install]
WantedBy=default.target
```

Enable + start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now cce-dashboard.service
journalctl --user -u cce-dashboard -f      # tail logs
```

For the MCP HTTP server (`cce serve --http`), use the same pattern with
`ExecStart=%h/.local/bin/cce serve --http --host 127.0.0.1 --port 8765` and
add `Environment=CCE_API_TOKEN=...` if you bind to a non-loopback host.

## launchd (macOS)

Save as `~/Library/LaunchAgents/com.cce.dashboard.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>com.cce.dashboard</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/cce</string>
        <string>dashboard</string>
        <string>--no-browser</string>
        <string>--port</string>
        <string>8080</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <!-- Generate: python -c "import secrets; print(secrets.token_urlsafe(32))" -->
        <key>CCE_DASHBOARD_TOKEN</key>
        <string>REPLACE_WITH_RANDOM_TOKEN</string>
    </dict>
    <key>KeepAlive</key>         <true/>
    <key>RunAtLoad</key>         <true/>
    <key>StandardOutPath</key>   <string>/tmp/cce-dashboard.out.log</string>
    <key>StandardErrorPath</key> <string>/tmp/cce-dashboard.err.log</string>
</dict>
</plist>
```

Load + start:

```bash
launchctl load -w ~/Library/LaunchAgents/com.cce.dashboard.plist
launchctl list | grep cce          # check it's running
```

The path to `cce` differs by install method — adjust `ProgramArguments[0]`:

| Install                     | Path                          |
|-----------------------------|-------------------------------|
| `uv tool install`           | `~/.local/bin/cce`            |
| `pipx install`              | `~/.local/bin/cce`            |
| `brew` (Apple Silicon)      | `/opt/homebrew/bin/cce`       |
| `brew` (Intel) / system pip | `/usr/local/bin/cce`          |

## Healthchecks

The HTTP server exposes `GET /health` (returns `{"status":"ok"}`) for liveness
probes. The dashboard does not — it serves an HTML page on `/`, so probe with
`GET /api/status` instead and check for HTTP 200.

```bash
# Dashboard liveness
curl -fsS http://localhost:8080/api/status > /dev/null

# HTTP serve mode liveness
curl -fsS http://localhost:8765/health > /dev/null
```

## Auth checklist

| Surface              | Default                                | Production setting                                      |
|----------------------|----------------------------------------|---------------------------------------------------------|
| Dashboard            | Open + CSRF-protected                  | Set `CCE_DASHBOARD_TOKEN` to a 32-byte random string    |
| MCP HTTP server      | Loopback open, non-loopback refused    | Set `CCE_API_TOKEN`; bind to loopback unless you must   |
| MCP stdio server     | No auth (only Claude Code talks to it) | n/a — Claude Code is the only client                    |

Generate tokens with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Store them outside the project tree — never commit the env file.

## Resource sizing

- **Memory.** Idle ~150-200 MB (mostly the embedding model). During an indexing
  run on a 1k-file project, peaks ~400-600 MB while a batch of chunks is in
  flight.
- **Disk.** Index size scales with chunk count: roughly 10-15 KB per chunk
  (vector + FTS + graph). A 5k-chunk project lands around 60-80 MB on disk.
- **CPU.** Embedding is CPU-bound and parallel (capped at 4 threads by
  default). Cold indexes are the only sustained load; query time is sub-100ms
  on a warm index.

## What still isn't supervised

- No multi-process / multi-host coordination. Two `cce serve` processes against
  the same project will both try to write the index — the pipeline lock
  prevents corruption inside a single process but does not coordinate across
  processes. Run one server per project per machine.
- No automatic backup of `~/.cce/projects/<name>/`. If you
  need durability, snapshot that directory in your normal backup flow — every
  file inside is replaceable from a fresh `cce index --full`, except for
  `sessions/*.json` which contain unique decisions and code-area notes.
