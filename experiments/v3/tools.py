"""Select an immutable task world on the original simulated MCP tools."""
import json
import re
import sys

from mcp.client.stdio import StdioServerParameters, stdio_client

from experiments.paths import ROOT
from experiments.tools import CASE_TOOLS, SIMULATED, DataForSEO
from .worlds import DATA


def task_data(task):
    if not re.fullmatch(r"C[45]-(?:0[1-9]|10)", task):
        raise ValueError("Unknown task")
    return json.loads((DATA / f"{task}.json").read_text())


def load_world(case, task):
    if not task.startswith(case + "-"):
        raise ValueError("Task belongs to another case")
    world = task_data(task)["world"]
    if case == "C5":
        for field in ("jobs", "logs"):
            world[field] = {int(k): v for k, v in world[field].items()}
    return world


class TaskTools(DataForSEO):
    def __init__(self, task):
        self.task = task
        case = task.split("-")[0]
        super().__init__(CASE_TOOLS[case], SIMULATED[case])

    def _transport(self):
        params = StdioServerParameters(command=sys.executable,
            args=["-m", self.module, "--v3-task", self.task], cwd=str(ROOT))
        return stdio_client(params)
