"""HTTP-only MCP adapter for the AgentsWatch management API."""

__version__ = "2.2.0"

from .api_client import AgentsWatchAPIClient
from .errors import AgentsWatchAPIError, ConfirmationRequiredError

__all__ = [
    "AgentsWatchAPIClient",
    "AgentsWatchAPIError",
    "ConfirmationRequiredError",
    "__version__",
]
