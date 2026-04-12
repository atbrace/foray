from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from foray.state import _atomic_write

TOOL_NAMES = ["claude", "uv", "git"]

PACKAGE_CHECKS = [
    ("cv2", "cv2.__version__"),
    ("numpy", "numpy.__version__"),
    ("PIL", "PIL.__version__"),
    ("torch", "torch.__version__"),
    ("sklearn", "sklearn.__version__"),
    ("pandas", "pandas.__version__"),
    ("matplotlib", "matplotlib.__version__"),
    ("scipy", "scipy.__version__"),
    ("requests", "requests.__version__"),
]


def _check_tools() -> str:
    lines = ["## CLI Tools"]
    for name in TOOL_NAMES:
        path = shutil.which(name)
        lines.append(f"- {name}: {path}" if path else f"- {name}: not found")
    return "\n".join(lines)


def _check_packages(project_root: Path) -> str:
    """Check package availability in the project's Python environment."""
    lines = ["## Python Packages"]
    for name, version_expr in PACKAGE_CHECKS:
        try:
            result = subprocess.run(
                ["uv", "run", "python", "-c", f"import {name}; print({version_expr})"],
                capture_output=True, text=True, timeout=10, cwd=project_root,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                lines.append(f"- {name}: {version}")
            else:
                lines.append(f"- {name}: not available")
        except (subprocess.TimeoutExpired, FileNotFoundError):
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
