"""Simulated MCP server for E2 on C4 (stdio): the tools of the original
server (`experiments/simulated/server_c4.py`) on the world of this package."""
import experiments.simulated.server_c4 as original
from experiments.simulated.v04 import world

original.W = world()                 # the tools read the module's world: only this process sees the change
app = original.app

if __name__ == "__main__":
    app.run()
