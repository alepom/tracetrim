"""The v3b task worlds on the original simulated C4 tools, over MCP stdio."""
import json
import re
import sys

from mcp.client.stdio import StdioServerParameters, stdio_client

from experiments.paths import ROOT
from experiments.tools import CASE_TOOLS, DataForSEO

from .worlds import DATA


def task_data(task):
    if not re.fullmatch(r"C4b-(?:0[1-9]|1[0-8])", task):
        raise ValueError("Unknown v3b task")
    return json.loads((DATA / f"{task}.json").read_text())


def load_world(task):
    return task_data(task)["world"]


class TaskTools(DataForSEO):
    def __init__(self, task):
        self.task = task
        super().__init__(CASE_TOOLS["C4"], "experiments.v3b.server_c4b")

    def _transport(self):
        return stdio_client(StdioServerParameters(command=sys.executable,
                                                  args=["-m", self.module, self.task], cwd=str(ROOT)))
