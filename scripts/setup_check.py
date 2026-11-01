"""Report Phase 1 prerequisites without requiring optional packages."""
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aatc.sumo_runtime import load_sumo_environment


def main():
    load_sumo_environment()
    print("Python: {} ({})".format(platform.python_version(), sys.executable))
    print("Python >= 3.11: {}".format(sys.version_info >= (3, 11)))
    home = os.environ.get("SUMO_HOME")
    print("SUMO_HOME: {}".format(home or "not set"))
    for executable in ("sumo", "sumo-gui", "netgenerate", "netconvert"):
        located = shutil.which(executable)
        if not located and home:
            candidates = [Path(home) / "bin" / (executable + ".exe"), Path(home) / "bin" / executable]
            located = next((str(path) for path in candidates if path.is_file()), None)
        print("{}: {}".format(executable, located or "not found"))
        if executable == "sumo" and located:
            result = subprocess.run([located, "--version"], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, universal_newlines=True)
            print("SUMO version: {}".format(result.stdout.strip().splitlines()[0] if result.stdout.strip() else "unknown"))
    tools = Path(home) / "tools" if home else None
    print("SUMO tools directory: {}".format(str(tools) if tools and tools.is_dir() else "not found"))
    if tools and tools.is_dir() and str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    print("TraCI import: {}".format(bool(importlib.util.find_spec("traci"))))
    print("sumolib import: {}".format(bool(importlib.util.find_spec("sumolib"))))
    for package in ("gymnasium", "stable_baselines3", "torch", "tensorboard", "langgraph",
                    "streamlit", "osmnx", "holidays"):
        print("{}: {}".format(package, bool(importlib.util.find_spec(package))))
    env_file = Path(__file__).resolve().parents[1] / ".env"
    configured_names = set()
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if value.strip():
                    configured_names.add(key.strip())
    has_key = bool(os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY")
                   or configured_names.intersection(("MODEL_API_KEY", "META_API_KEY")))
    has_model = bool(os.environ.get("META_MODEL") or "META_MODEL" in configured_names)
    has_signal_key = bool(os.environ.get("DATA_GO_KR_SERVICE_KEY")
                          or "DATA_GO_KR_SERVICE_KEY" in configured_names)
    has_signal_api_root = bool(os.environ.get("DATA_GO_KR_API_ROOT")
                               or "DATA_GO_KR_API_ROOT" in configured_names)
    print("Meta Model API key configured: {}".format(has_key))
    print("Meta model configured: {}".format(has_model))
    print("Government signal API key configured: {}".format(has_signal_key))
    print("Government signal API base URL configured: {}".format(has_signal_api_root))
    print("Meta API configuration: not required for simulation or RL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
