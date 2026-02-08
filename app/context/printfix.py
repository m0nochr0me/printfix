"""
Printfix MCP server
"""

from typing import Annotated, Any

from click import UUID
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

__all__ = ("server",)

server = FastMCP("Printfix")


# TODO