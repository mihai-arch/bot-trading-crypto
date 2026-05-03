"""
Entry point: python -m bit.dashboard

Starts the dashboard on http://127.0.0.1:8765
"""

import uvicorn

uvicorn.run(
    "bit.dashboard.app:app",
    host="127.0.0.1",
    port=8765,
    reload=False,
    log_level="info",
)
