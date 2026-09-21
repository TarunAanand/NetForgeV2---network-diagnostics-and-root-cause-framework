"""Configuration for the M2 controller process."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ControllerConfig:
    controller_token: str
    agent_token: str
    database_path: str = ".netforge_controller.db"

    @classmethod
    def from_env(cls) -> "ControllerConfig":
        controller_token = os.environ.get("NETFORGE_CONTROLLER_TOKEN", "")
        agent_token = os.environ.get("NETFORGE_AGENT_TOKEN", "")
        if not controller_token or not agent_token:
            raise ValueError("NETFORGE_CONTROLLER_TOKEN and NETFORGE_AGENT_TOKEN are required")
        return cls(controller_token=controller_token, agent_token=agent_token, database_path=os.environ.get("NETFORGE_CONTROLLER_DB", ".netforge_controller.db"))
