from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from foray.state import _atomic_write

TOOL_NAMES = ["claude", "uv", "git"]

PACKAGE_NAMES = ["cv2", "numpy", "PIL", "torch", "sklearn", "pandas", "matplotlib", "scipy", "requests"]

_CHECK_SCRIPT = """
import importlib, importlib.util, json
packages = %r
results = {}
for name in packages:
    spec = importlib.util.find_spec(name)
    if spec is None:
        results[name] = None
        continue
    try:
        mod = importlib.import_module(name)
        results[name] = getattr(mod, "__version__", "installed")
    except Exception:
        results[name] = "error"
print(json.dumps(results))
"""


def _check_tools() -> str:
    lines = ["## CLI Tools"]
    for name in TOOL_NAMES:
        path = shutil.which(name)
        lines.append(f"- {name}: {path}" if path else f"- {name}: not found")
    return "\n".join(lines)


def _check_packages(project_root: Path) -> str:
    """Check package availability in the project's Python environment."""
    lines = ["## Python Packages"]
    try:
        result = subprocess.run(
            ["uv", "run", "python", "-c", _CHECK_SCRIPT % (PACKAGE_NAMES,)],
            capture_output=True, text=True, timeout=30, cwd=project_root,
        )
        if result.returncode == 0:
            packages = json.loads(result.stdout)
            for name in PACKAGE_NAMES:
                version = packages.get(name)
                if version is None:
                    lines.append(f"- {name}: not available")
                elif version == "error":
                    lines.append(f"- {name}: found but failed to import")
                else:
                    lines.append(f"- {name}: {version}")
        else:
            for name in PACKAGE_NAMES:
                lines.append(f"- {name}: not available")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        for name in PACKAGE_NAMES:
            lines.append(f"- {name}: not available")
    return "\n".join(lines)


def run_preflight(foray_dir: Path, project_root: Path | None = None) -> None:
    """Run environment pre-flight checks and write results to environment.md."""
    if project_root is None:
        project_root = foray_dir.parent
    sections = [
        "# Environment",
        "",
        _check_tools(),
        "",
        _check_packages(project_root),
    ]
    _atomic_write(foray_dir / "environment.md", "\n".join(sections) + "\n")
