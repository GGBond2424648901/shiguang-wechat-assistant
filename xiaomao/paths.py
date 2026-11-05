"""Keep installed application files separate from the user's settings and data."""
import os
from pathlib import Path
import sys


def default_root() -> Path:
    source = Path(__file__).resolve().parent.parent
    if not getattr(sys, "frozen", False) and (source / "pyproject.toml").is_file():
        return source
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "XiaomaoAssistant"
