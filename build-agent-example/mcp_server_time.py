from datetime import datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("time")


@mcp.tool()
def get_current_time() -> str:
    """Return the current local time as an ISO-like string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool()
def get_current_date() -> str:
    """Return the current local date."""
    return datetime.now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    mcp.run()
