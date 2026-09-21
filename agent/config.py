"""Agent configuration. Bearer tokens are demo-only; mTLS follows in M2."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    bearer_token: str
    hostname: str = field(default_factory=socket.gethostname)
    allowed_targets: frozenset[str] = field(default_factory=frozenset)
    topology_tags: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "AgentConfig":
        agent_id = os.environ.get("NETFORGE_AGENT_ID", "").strip()
        token = os.environ.get("NETFORGE_AGENT_TOKEN", "")
        if not agent_id or not token:
            raise ValueError("NETFORGE_AGENT_ID and NETFORGE_AGENT_TOKEN are required")
        targets = frozenset(
            item.strip()
            for item in os.environ.get("NETFORGE_ALLOWED_TARGETS", "").split(",")
            if item.strip()
        )
        tags = {
            key.removeprefix("NETFORGE_TAG_").lower(): value
            for key, value in os.environ.items()
            if key.startswith("NETFORGE_TAG_") and value
        }
        return cls(agent_id=agent_id, bearer_token=token, allowed_targets=targets, topology_tags=tags)
