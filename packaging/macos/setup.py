"""py2app definition for the standalone macOS application."""

from __future__ import annotations

import os
import platform
import sys
import tomllib
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
METADATA = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
VERSION = METADATA["project"]["version"]
ARCHITECTURE = os.environ.get("MACOS_ARCH", platform.machine())
ICON_PATH = os.environ.get(
    "MACOS_ICON_PATH", str(Path(__file__).with_name("AppIcon.icns"))
)

if ARCHITECTURE not in {"arm64", "x86_64", "universal2"}:
    raise RuntimeError(f"Unsupported macOS build architecture: {ARCHITECTURE}")

includes = [
    "app.resources",
    "keyring.backends.macOS",
    "keyring.backends.macOS.api",
]

setup(
    name="NYC Housing Research",
    version=VERSION,
    app=[
        {
            "script": str(Path(__file__).with_name("desktop_entry.py")),
            "prescripts": [str(Path(__file__).with_name("no_bytecode.py"))],
        }
    ],
    package_dir={"": str(ROOT)},
    options={
        "py2app": {
            "arch": ARCHITECTURE,
            # Uvicorn selects its event loop, lifespan, HTTP, and websocket
            # implementations by import string at runtime. Bundle the package
            # whole so modulegraph does not discard those dynamic imports.
            "packages": ["app", "anyio", "uvicorn"],
            "includes": includes,
            "iconfile": ICON_PATH,
            "plist": {
                "CFBundleDisplayName": "NYC Housing Research",
                "CFBundleIdentifier": "org.nychousingresearch.app",
                "CFBundleName": "NYC Housing Research",
                "CFBundleShortVersionString": VERSION,
                "CFBundleVersion": VERSION,
                "LSApplicationCategoryType": "public.app-category.reference",
                "LSMinimumSystemVersion": "12.0",
                "NSHighResolutionCapable": True,
                "NSHumanReadableCopyright": "Copyright © 2026 NYC Housing Research",
                "NSRequiresAquaSystemAppearance": False,
            },
        }
    },
)
