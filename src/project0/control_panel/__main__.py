"""Entry point: ``uv run python -m project0.control_panel``.

Binds to 0.0.0.0:8090. Tailscale-gated by deployment convention — same
as the Intelligence webapp. The supervisor is constructed with the real
spawn_fn (uv run python -m project0.main).
"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from project0.control_panel.app import create_app
from project0.control_panel.supervisor import MAASSupervisor
from project0.store import Store


def main() -> None:
    project_root = Path.cwd()
    store_path = project_root / "data" / "store.db"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(store_path)
    store.init_schema()

    supervisor = MAASSupervisor()  # real spawn_fn
    app = create_app(
        supervisor=supervisor,
        store=store,
        project_root=project_root,
    )
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")


if __name__ == "__main__":
    main()
