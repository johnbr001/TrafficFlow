"""Generate the runnable SUMO network from editable plain XML inputs."""
import subprocess
from pathlib import Path

from .sumo_runtime import find_sumo_tool


def generate_network(directory=None):
    simulation_dir = Path(directory) if directory else Path(__file__).resolve().parent.parent / "simulation"
    output = simulation_dir / "intersection.net.xml"
    command = [
        find_sumo_tool("netconvert"),
        "--node-files", str(simulation_dir / "nodes.nod.xml"),
        "--edge-files", str(simulation_dir / "edges.edg.xml"),
        "--connection-files", str(simulation_dir / "connections.con.xml"),
        "--tllogic-files", str(simulation_dir / "signals.tll.xml"),
        "--output-file", str(output),
        "--no-turnarounds", "true",
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if result.returncode:
        raise RuntimeError("netconvert failed (exit {}):\n{}".format(result.returncode, result.stdout.strip()))
    if not output.is_file():
        raise RuntimeError("netconvert exited successfully but did not create {}".format(output))
    return output
