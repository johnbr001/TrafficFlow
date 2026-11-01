"""Locate SUMO and make the TraCI modules bundled with SUMO importable."""
import os
import shutil
import sys
from pathlib import Path


def load_sumo_environment():
    """Read only SUMO settings from the project .env; never inspect API secrets."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.is_file():
        return
    allowed = ("SUMO_HOME", "SUMO_BINARY", "SUMO_GUI_BINARY")
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in allowed or os.environ.get(key):
            continue
        value = value.strip().strip("\"'")
        if value:
            os.environ[key] = value


def configure_sumo_tools():
    load_sumo_environment()
    home = os.environ.get("SUMO_HOME")
    if not home:
        raise RuntimeError("SUMO_HOME is not set. Install SUMO and point SUMO_HOME to its installation directory.")
    tools = Path(home) / "tools"
    if not tools.is_dir():
        raise RuntimeError("SUMO_HOME does not contain a tools directory: {}".format(tools))
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import traci
        import sumolib
    except ImportError as exc:
        raise RuntimeError("TraCI/sumolib were not found under SUMO_HOME/tools: {}".format(exc))
    return traci, sumolib


def find_sumo(gui=False):
    load_sumo_environment()
    variable = "SUMO_GUI_BINARY" if gui else "SUMO_BINARY"
    configured = os.environ.get(variable)
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        raise RuntimeError("{} points to a missing executable: {}".format(variable, path))
    executable = "sumo-gui" if gui else "sumo"
    located = shutil.which(executable)
    if located:
        return located
    home = os.environ.get("SUMO_HOME")
    if home:
        candidates = [Path(home) / "bin" / (executable + ".exe"), Path(home) / "bin" / executable]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError("Could not find {}. Add it to PATH or set {}.".format(executable, variable))


def find_sumo_tool(name):
    load_sumo_environment()
    home = os.environ.get("SUMO_HOME")
    if home:
        for candidate in (Path(home) / "bin" / (name + ".exe"), Path(home) / "bin" / name):
            if candidate.is_file():
                return str(candidate)
    located = shutil.which(name)
    if located:
        return located
    raise RuntimeError("Could not find {}. Add SUMO's bin directory to PATH or set SUMO_HOME.".format(name))
