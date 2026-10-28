from .bridge import Bridge
from .config import Config

INSTRUCTIONS = "JobSeer inbox and tracker actions. Every result names the target server it ran against."


def build(config: Config, client=None, confirms=None):
    from mcp.server.fastmcp import FastMCP

    bridge = Bridge(config, client, confirms)
    server = FastMCP("jobseer", instructions=INSTRUCTIONS, log_level="WARNING")
    bridge.register(server)
    return server, bridge
