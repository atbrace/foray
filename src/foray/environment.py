from __future__ import annotations

import importlib
import importlib.util
import shutil
from pathlib import Path

from foray.state import _atomic_write

TOOL_NAMES = ["claude", "uv", "git"]

PACKAGE_NAMES = [
    "cv2",
    "numpy",
    "PIL",
    "torch",
    "sklearn",
    "pandas",
    "matplotlib",
    "scipy",
    "requests",
]


def _check_tools() -> str:
    lines = ["## CLI Tools"]
    for name in TOOL_NAMES:
        path = shutil.which(name)
        lines.append(f"- {name}: {path}" if path else f"- {name}: not found")
    return "\n".join(lines)


def _check_packages() -> str:
    lines = ["## Python Packages"]
    for name in PACKAGE_NAMES:
        spec = importlib.util.find_spec(name)
        if spec is None:
            lines.append(f"- {name}: not available")
            continue
        try:
            mod = importlib.import_module(name)
            version = getattr(mod, "__version__", "installed")
            lines.append(f"- {name}: {version}")
        except Exception:
            lines.append(f"- {name}: found but failed to import")
    return "\n".join(lines)


def run_preflight(foray_dir: Path) -> None:
    """Run environment pre-flight checks and write results to environment.md."""
    sections = [
        "# Environment",
        "",
        _check_tools(),
        "",
        _check_packages(),
    ]
    _atomic_write(foray_dir / "environment.md", "\n".join(sections) + "\n")
