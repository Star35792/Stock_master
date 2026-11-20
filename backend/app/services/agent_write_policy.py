"""Explicit write scopes; enabling maintenance never enables research writes."""

from app.config.settings import settings
from app.services.agent_maintenance_contract import MAINTENANCE_ACTIONS

DATA_MAINTENANCE_TOOLS = frozenset(MAINTENANCE_ACTIONS)


def write_enabled(tool: str) -> bool:
    return settings.agent_write_enabled or (
        settings.agent_data_maintenance_enabled and tool in DATA_MAINTENANCE_TOOLS
    )
