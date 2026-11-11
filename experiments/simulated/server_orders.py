"""Simulated MCP server for the adaptive-learning demo (stdio): see orders.py."""
import sys

from mcp.server.mcpserver import MCPServer

from experiments.simulated import orders

app = MCPServer("orders-sim")


@app.tool()
def list_orders(customer: str, limit: int = 3) -> str:
    """List the orders of a customer, newest first. limit: 1-50."""
    if len(sys.argv) > 1:                                 # a file to log calls to: tests count real executions
        with open(sys.argv[1], "a") as fh:
            fh.write(f"{customer}\n")
    return orders.output(customer, limit)


if __name__ == "__main__":
    app.run()
