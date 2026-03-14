"""Simemu allocation dashboard — served at GET /"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.responses import HTMLResponse

_STYLE = """
  body { background:#0e0e0e; color:#e0e0e0; font-family:'JetBrains Mono',monospace; font-size:13px; padding:24px; margin:0 }
  h1 { color:#00d9b8; font-size:16px; margin:0 0 16px }
  table { border-collapse:collapse; width:100% }
  th { color:#888; font-weight:normal; text-align:left; padding:4px 12px 8px 0; border-bottom:1px solid #222 }
  td { padding:6px 12px 6px 0; border-bottom:1px solid #1a1a1a }
  .online { color:#00d9b8 }
  .idle { color:#555 }
"""


def _build_html(allocations: dict) -> str:
    rows = ""
    for slug, a in allocations.items():
        idle = ""
        if a.heartbeat_at:
            delta = (datetime.now(timezone.utc) - datetime.fromisoformat(a.heartbeat_at)).total_seconds()
            idle = f"{int(delta // 60)}m ago"
        rows += (
            f"<tr>"
            f"<td class='online'>{slug}</td>"
            f"<td>{a.platform}</td>"
            f"<td>{a.device_name}</td>"
            f"<td>{a.agent or '—'}</td>"
            f"<td class='idle'>{idle}</td>"
            f"</tr>"
        )
    if not rows:
        rows = "<tr><td colspan='5' style='color:#555;padding-top:16px'>No active allocations</td></tr>"
    return f"""<!DOCTYPE html>
<html><head>
<meta charset='utf-8'>
<meta http-equiv='refresh' content='5'>
<title>simemu</title>
<style>{_STYLE}</style>
</head><body>
<h1>simemu</h1>
<table>
  <thead><tr><th>slug</th><th>platform</th><th>device</th><th>agent</th><th>idle</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
</body></html>"""


def register_dashboard(app, get_state):
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_root():
        return HTMLResponse(content=_build_html(get_state()))
