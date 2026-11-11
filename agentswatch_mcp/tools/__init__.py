"""Tool registration modules for the AgentsWatch MCP service."""

from .assets import register_asset_tools
from .automation import register_automation_tools
from .library import register_library_tools

__all__ = [
    "register_asset_tools",
    "register_automation_tools",
    "register_library_tools",
]
