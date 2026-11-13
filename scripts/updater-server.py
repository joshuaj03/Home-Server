#!/usr/bin/env python3
"""Lightweight host HTTP service to trigger and monitor /home/jake/HomeServer/scripts/update.sh from Glance."""

from datetime import datetime
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

BASE_DIR = Path(__file__).resolve().parent.parent
UPDATE_SCRIPT = BASE_DIR / "scripts" / "update.sh"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "update.log"
STATUS_FILE = LOG_DIR / "update-status.json"

PORT = int(os.environ.get("UPDATER_PORT", "9911"))
TOKEN = os.environ.get("UPDATER_TOKEN", "")

_lock = threading.Lock()
_state = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "last_line": "Ready to update",
}


def _now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _load_persisted_state() -> None:
    if STATUS_FILE.exists():
        try:
            saved = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            if saved.get("status") == "running":
                saved["status"] = "error"
                saved["last_line"] = "Interrupted by service restart"
            _state.update(saved)
        except Exception:
            pass


def _save_state() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(_state, indent=2), encoding="utf-8")


def _read_log_tail(max_lines: int = 80) -> str:
    if not LOG_FILE.exists():
        return ""
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"Unable to read log: {exc}"


def _run_update_worker() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_FILE.open("w", encoding="utf-8") as log_fp:
            log_fp.write(f"[{_now_iso()}] Starting {UPDATE_SCRIPT}\n")
            log_fp.flush()

            proc = subprocess.Popen(
                ["/bin/bash", str(UPDATE_SCRIPT)],
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                log_fp.write(line + "\n")
                log_fp.flush()
                if line.strip():
                    with _lock:
                        _state["last_line"] = line.strip()[:120]

            rc = proc.wait()
            log_fp.write(f"\n[{_now_iso()}] Finished with exit code {rc}\n")
            log_fp.flush()

        with _lock:
            _state["status"] = "success" if rc == 0 else "error"
            _state["finished_at"] = _now_iso()
            _state["exit_code"] = rc
            _state["last_line"] = (
                f"Updated at {_state['finished_at'].split(' ')[1]}"
                if rc == 0
                else f"Failed (exit {rc}) — check logs"
            )
            _save_state()
    except Exception as exc:
        with _lock:
            _state["status"] = "error"
            _state["finished_at"] = _now_iso()
            _state["exit_code"] = -1
            _state["last_line"] = f"Error: {exc}"[:120]
            _save_state()


def _render_widget_html() -> str:
    escaped_token = html.escape(TOKEN, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Container Updates</title>
<style>
  :root {{
    --bg: #121212;
    --card-bg: #121212;
    --border: #1e1e1c;
    --text: #d9d9d8;
    --muted: #8a8a85;
    --primary: #fc7f2c;
    --ok: #4ade80;
    --err: #f87171;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .card {{
    width: 100%;
    height: 100%;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }}
  .info {{
    min-width: 0;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }}
  .title-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.85rem;
    font-weight: 600;
  }}
  .dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--muted);
    flex-shrink: 0;
  }}
  .dot.running {{ background: var(--primary); animation: pulse 1.1s infinite ease-in-out; }}
  .dot.success {{ background: var(--ok); }}
  .dot.error {{ background: var(--err); }}
  .status-line {{
    font-size: 0.74rem;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .links {{
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.7rem;
  }}
  .links a {{
    color: var(--primary);
    text-decoration: none;
    opacity: 0.9;
  }}
  .links a:hover {{ text-decoration: underline; opacity: 1; }}
  .btn {{
    width: 48px;
    height: 48px;
    border-radius: 12px;
    border: 1px solid #2b2b27;
    background: #1a1a18;
    color: var(--primary);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: transform 0.12s ease, border-color 0.15s ease, background 0.15s ease;
  }}
  .btn:hover:not(:disabled) {{
    border-color: var(--primary);
    background: #22201c;
    transform: translateY(-1px);
  }}
  .btn:active:not(:disabled) {{ transform: translateY(0); }}
  .btn:disabled {{ cursor: wait; opacity: 0.85; }}
  .btn.running svg {{ animation: spin 1.1s linear infinite; }}
  .btn.success {{ color: var(--ok); border-color: rgba(74, 222, 128, 0.35); }}
  .btn.error {{ color: var(--err); border-color: rgba(248, 113, 113, 0.35); }}
  @keyframes spin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.35; }} }}
</style>
</head>
<body>
  <div class="card">
    <div class="info">
      <div class="title-row">
        <span id="dot" class="dot"></span>
        <span id="headline">Update Stack</span>
      </div>
      <div id="statusLine" class="status-line">Checking status...</div>
      <div class="links">
        <a href="/logs" target="_blank" rel="noopener">View Logs ↗</a>
      </div>
    </div>
    <button id="updateBtn" class="btn" title="Pull images, rebuild flask_app, and recreate updated containers" aria-label="Update Containers">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
        <path d="M3 3v5h5"/>
        <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
        <path d="M16 16h5v5"/>
      </svg>
    </button>
  </div>

<script>
  const TOKEN = "{escaped_token}";
  const btn = document.getElementById("updateBtn");
  const dot = document.getElementById("dot");
  const headline = document.getElementById("headline");
  const statusLine = document.getElementById("statusLine");
  let pollTimer = null;

  function applyState(data) {{
    const st = data.status || "idle";
    dot.className = "dot " + st;
    btn.className = "btn " + st;
    btn.disabled = st === "running";

    if (st === "running") {{
      headline.textContent = "Updating...";
      statusLine.textContent = data.last_line || "Running update.sh...";
      schedulePoll(1500);
    }} else if (st === "success") {{
      headline.textContent = "Up to date";
      statusLine.textContent = data.last_line || ("Finished " + (data.finished_at || ""));
      schedulePoll(15000);
    }} else if (st === "error") {{
      headline.textContent = "Update failed";
      statusLine.textContent = data.last_line || "Check logs";
      schedulePoll(15000);
    }} else {{
      headline.textContent = "Update Stack";
      statusLine.textContent = data.last_line || "Click icon to run update.sh";
      schedulePoll(15000);
    }}
  }}

  function schedulePoll(ms) {{
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(fetchStatus, ms);
  }}

  async function fetchStatus() {{
    try {{
      const res = await fetch("/api/status", {{ cache: "no-store" }});
      if (res.ok) applyState(await res.json());
      else schedulePoll(5000);
    }} catch (_) {{
      schedulePoll(3000);
    }}
  }}

  btn.addEventListener("click", async () => {{
    btn.disabled = true;
    dot.className = "dot running";
    btn.className = "btn running";
    headline.textContent = "Starting...";
    statusLine.textContent = "Launching update.sh...";
    try {{
      const res = await fetch("/api/update", {{
        method: "POST",
        headers: {{ "X-Update-Token": TOKEN }},
      }});
      const data = await res.json();
      applyState(data);
    }} catch (err) {{
      headline.textContent = "Request failed";
      statusLine.textContent = String(err);
      btn.disabled = false;
      schedulePoll(3000);
    }}
  }});

  fetchStatus();
</script>
</body>
</html>
"""


def _render_logs_html() -> str:
    escaped_token = html.escape(TOKEN, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HomeServer Update Logs</title>
<style>
  body {{
    margin: 0;
    padding: 20px;
    background: #121212;
    color: #d9d9d8;
    font-family: "JetBrains Mono", ui-monospace, monospace;
  }}
  .top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
    gap: 12px;
  }}
  h1 {{ font-size: 1.05rem; margin: 0; color: #fc7f2c; }}
  button {{
    background: #1a1a18;
    color: #fc7f2c;
    border: 1px solid #fc7f2c;
    border-radius: 8px;
    padding: 8px 14px;
    font-family: inherit;
    cursor: pointer;
  }}
  button:disabled {{ opacity: 0.6; cursor: wait; }}
  pre {{
    background: #0b0b0a;
    border: 1px solid #222;
    border-radius: 10px;
    padding: 14px;
    overflow: auto;
    max-height: 80vh;
    font-size: 0.82rem;
    line-height: 1.45;
    white-space: pre-wrap;
  }}
</style>
</head>
<body>
  <div class="top">
    <h1>HomeServer Update Logs — <span id="st">loading...</span></h1>
    <button id="runBtn">Run Update Now</button>
  </div>
  <pre id="logBox">Loading...</pre>
<script>
  const TOKEN = "{escaped_token}";
  const st = document.getElementById("st");
  const logBox = document.getElementById("logBox");
  const runBtn = document.getElementById("runBtn");

  async function refresh() {{
    try {{
      const res = await fetch("/api/status", {{ cache: "no-store" }});
      const data = await res.json();
      st.textContent = data.status + (data.finished_at ? " (finished " + data.finished_at + ")" : "");
      runBtn.disabled = data.status === "running";
      logBox.textContent = data.log_tail || "No update logs yet.";
      logBox.scrollTop = logBox.scrollHeight;
    }} catch (e) {{
      st.textContent = "reconnecting...";
    }}
  }}

  runBtn.addEventListener("click", async () => {{
    await fetch("/api/update", {{
      method: "POST",
      headers: {{ "X-Update-Token": TOKEN }},
    }});
    refresh();
  }});

  refresh();
  setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Update-Token")

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code: int, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(code)
        self._send_cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_html(200, _render_widget_html())
        elif path == "/logs":
            self._send_html(200, _render_logs_html())
        elif path == "/health":
            self._send_json(200, {"ok": True})
        elif path == "/api/status":
            with _lock:
                payload = dict(_state)
            payload["log_tail"] = _read_log_tail()
            self._send_json(200, payload)
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/update":
            self._send_json(404, {"error": "Not found"})
            return

        supplied = self.headers.get("X-Update-Token", "")
        if not TOKEN or not hmac.compare_digest(supplied, TOKEN):
            self._send_json(403, {"error": "Invalid or missing X-Update-Token"})
            return

        with _lock:
            if _state["status"] == "running":
                payload = dict(_state)
                self._send_json(409, payload)
                return
            _state["status"] = "running"
            _state["started_at"] = _now_iso()
            _state["finished_at"] = None
            _state["exit_code"] = None
            _state["last_line"] = "Starting update script..."
            _save_state()
            payload = dict(_state)

        threading.Thread(target=_run_update_worker, daemon=True).start()
        self._send_json(202, payload)


def main() -> None:
    _load_persisted_state()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"homeserver-updater listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

