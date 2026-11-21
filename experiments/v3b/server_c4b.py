"""The simulated C4 billing server of protocol v3 (experiments/simulated/server_c4.py), unchanged,
serving one v3b task world: `python -m experiments.v3b.server_c4b C4b-NN`."""
import sys

from experiments.simulated import server_c4

from .tools import load_world

if __name__ == "__main__":
    server_c4.W = load_world(sys.argv[1])
    server_c4.app.run()
