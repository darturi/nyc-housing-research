"""Exercise the local service using only modules inside a built app bundle."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx

from app.launcher import LocalApplicationServer, prepare_workspace
from app.workspace.context import WorkspaceContext


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nyc-housing-macos-smoke-") as data_dir:
        environment = dict(os.environ)
        environment["NYC_HOUSING_DATA_DIR"] = data_dir
        environment["NYC_HOUSING_OFFLINE"] = "1"
        context = WorkspaceContext.from_options(
            Path(data_dir),
            environment=environment,
        )
        storage = prepare_workspace(context)
        storage.close()
        with LocalApplicationServer(context, port=0) as runtime:
            runtime.start_background()
            response = httpx.get(runtime.url, trust_env=False, timeout=10)
            response.raise_for_status()
            if "NYC Housing Research" not in response.text:
                raise RuntimeError("The bundled application returned unexpected HTML.")
    print("Bundled local service smoke test passed.")


if __name__ == "__main__":
    main()
