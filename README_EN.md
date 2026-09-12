# Hermes Watch 🐚

> AI server inspection & visualization platform — bring the AI ops agent back to your own rack.

English · [中文](README.md)

**In one sentence**: Grafana/Netdata lock AI behind cloud paywalls and per-host pricing. Hermes Watch lets an ops-savvy agent live on your own machine — it inspects local and cloud servers automatically, digs into root causes, and produces visual health reports. Your data never leaves the box.

## Why

Existing self-hosted monitoring picks two of three: light deployment, solid alerting, AI depth. Enterprise AI Ops (Keep, kagent, holmesgpt) ingests alerts from cloud tools and needs a fleet of backing services. Lightweight probes (Beszel, Gatus) polish collection but have no diagnosis. Hermes Watch occupies the empty spot: **the diagnose → approve → execute closed loop, inside a single self-hosted box that runs on 2GB of RAM**.

## Features

- **Inspection engine** — SSH read-only probes (asyncssh, concurrency-limited) or an outbound agent (Go single binary, HMAC-signed push, no inbound ports); built-in demo fleet simulator with live-walking curves
- **Rule engine first** — deterministic threshold rules with hysteresis (trigger/clear lines, editable in Settings) → findings + weighted health score
- **Alert lifecycle** (Uptime Kuma / Gatus / Netdata school) — auto-recovery with duration in the notice, crit re-notify interval, quiet hours (midnight-crossing), 10 notification channels (WeCom / DingTalk / Feishu / Telegram / ServerChan / generic Webhook / Discord / Slack / ntfy / SMTP) with templates, test button and delivery log
- **Service probes** — URL / TCP / DNS / Push kinds, Gatus-style conditions (status code + keyword + max latency + cert days left) and dual-threshold debounce; heartbeat strips; status badges as SVG
- **Diagnosis with evidence** — per-finding deep-dive probes (`du`, `ps`, `last`, `systemctl`) → root-cause card + evidence chain you can click back into
- **Proposals, not actions** — fixes are cards; the agent never touches a machine by itself. Approval records the decision; execution is a separate explicit step behind segment-by-segment command whitelisting (regex full-match, blocks `rm -rf` and pipe injection), 20s timeout, fully audited
- **AI chat with tools** — the chat page lets the LLM call the same read-only toolset as the local MCP endpoint (fleet status, findings, metrics, probes, events) before answering; thinking traces and tool calls are shown collapsed. OpenAI-compatible endpoints (BYO, incl. Ollama); falls back to a local rules summary when AI is off
- **Local MCP** — read-only streamable-HTTP endpoint (8 tools) for Claude Desktop / Cursor
- **Web terminal** — WebSocket → asyncssh PTY; bastion (jump-host) chained SSH with connection pooling; SSH TOFU host-key confirmation; port-baseline drift alerts
- **Reports & status page** — scheduled HTML health reports (theme-aware), public read-only status page and SVG badges behind revocable tokens, PWA
- **Multi-user RBAC** — admin / operator (on-duty: terminal, approvals, alert ack, probes, AI chat) / observer (read-only); role-signed sessions, navigation filtered by role
- **Ops niceties** — one-line install/upgrade script (optional systemd), SQLite with daily auto-backup and one-click restore, i18n (中文/English), day/night themes with live chart recoloring

## Architecture

```
Browser (React + ECharts + xterm.js, :5273)
   │ /api proxy · /ws WebSocket
Backend (FastAPI + SQLite, :8800)
   ├─ Collection   SSH read-only probes / demo simulator / outbound agent (push)
   ├─ Probes       URL/TCP/DNS/Push, dual-threshold debounce, heartbeat strips
   ├─ Rule engine  thresholds + hysteresis → findings + health score
   ├─ Alerts       auto-recovery · re-notify · quiet hours · 10 channels
   ├─ Analysis     deep-dive probes → root-cause card + evidence chain
   ├─ AI chat      tool-calling loop over read-only fleet tools (collapsible traces)
   ├─ Proposals    approve → execute (whitelisted, audited)
   ├─ Terminal     WebSocket → asyncssh PTY (bastion chains supported)
   ├─ Local MCP    read-only streamable-HTTP, 8 tools
   └─ Reports      structured data → local HTML health reports
```

## Quick start

**Linux / macOS one-line install** (deps + frontend build + optional systemd unit):

```bash
git clone https://github.com/StarrySea1412/hermes-watch.git && cd hermes-watch
./install.sh                 # or: sudo ./install.sh --systemd
./install.sh --update        # later upgrades
```

**Windows (manual)**:

```powershell
# Backend (Python 3.11+)
cd backend
py -3.11 -m venv venv
./venv/Scripts/python -m pip install fastapi "uvicorn[standard]" asyncssh httpx
./venv/Scripts/python run.py        # http://127.0.0.1:8800

# Frontend
cd frontend
npm install
npm run dev                          # http://localhost:5273
```

**Production (single process)**: `cd frontend && npm run build` — the backend serves `frontend/dist`, so `http://127.0.0.1:8800` alone is the whole panel (SPA routes, static assets, PWA). **Docker**: `docker build -t hermes-watch . && docker run -p 8800:8800 -v hermes-data:/data hermes-watch`.

First start seeds a 4-host demo fleet (web-1 healthy / db-1 disk full / app-1 memory leak / cache-1 suspicious login) and auto-triggers diagnosis — the full alert → diagnosis → proposal pipeline works out of the box.

**Connecting real servers**: generate a token in the Enroll page and run the [Go agent](agent-go/README.md) on the target (or the `hermes-watch-agent.sh` pure sh+curl variant) — no inbound ports needed; or add SSH hosts in Settings. Point an MCP client (Claude Desktop / Cursor) at `http://127.0.0.1:8800/api/mcp` to query inspection data from your editor.

## Design principles (from competitive research, see `docs/`)

1. The rule engine states facts first; AI only narrates and deep-dives (never real-time detection via LLM)
2. Every AI conclusion carries an evidence chain that clicks back into raw command output
3. Proposals only: the agent never modifies a machine directly; execution is whitelisted, audited, default-off
4. AI outbound is off by default; nothing leaves the machine until you enable it (anonymization optional)
5. Deployment stays light: single-file SQLite, no message queues, no service mesh

## Releases

Tag `v*` publishes cross-compiled agent binaries (linux/amd64, linux/arm64, windows/amd64) via GitHub Actions. See [Releases](https://github.com/StarrySea1412/hermes-watch/releases).

The full feature roadmap lives in the [中文 README](README.md) (bilingual project — docs and the panel UI are Chinese-first with full English i18n).
