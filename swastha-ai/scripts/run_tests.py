"""Run pytest using the repo-local .venv when available."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def preferred_python() -> str:
    candidate = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def main() -> int:
    args = sys.argv[1:] or ["tests/ai_core", "tests/compliance"]
    command = [preferred_python(), "-m", "pytest", *args]
    print("+ " + " ".join(command))
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
