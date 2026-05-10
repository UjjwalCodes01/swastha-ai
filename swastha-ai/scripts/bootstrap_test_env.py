"""Create a local cross-platform test virtualenv and install test deps."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.check_call(command, cwd=ROOT)


def main() -> None:
    if not venv_python().exists():
        run([sys.executable, "-m", "venv", str(VENV)])

    py = str(venv_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-r", str(ROOT / "requirements-test.txt")])
    print(f"Test environment ready: {py}")


if __name__ == "__main__":
    main()
