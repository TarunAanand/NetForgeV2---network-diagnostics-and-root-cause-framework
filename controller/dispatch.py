"""Bounded concurrent HTTP dispatch from controller to registered agents."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.models import ProbeRequest
from controller.models import RegisteredAgent
from core.remote_observation import AgentObservation


class AgentDispatchError(RuntimeError):
    pass


class AgentDispatcher:
    def __init__(self, agent_bearer_token: str, max_workers: int = 8):
        self.agent_bearer_token = agent_bearer_token
        self.max_workers = max_workers

    def probe(self, agent: RegisteredAgent, request: ProbeRequest) -> list[AgentObservation]:
        url = f"{str(agent.url).rstrip('/')}/v1/probe"
        body = request.model_dump_json().encode("utf-8")
        http_request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.agent_bearer_token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(http_request, timeout=request.timeout_seconds + 5) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise AgentDispatchError(str(exc)) from exc
        return [AgentObservation.model_validate(item) for item in payload.get("results", [])]

    def fanout(self, agents: list[RegisteredAgent], request: ProbeRequest) -> tuple[dict[str, list[AgentObservation]], dict[str, str]]:
        observations: dict[str, list[AgentObservation]] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(agents))) as executor:
            futures = {executor.submit(self.probe, agent, request): agent.agent_id for agent in agents}
            for future in as_completed(futures):
                agent_id = futures[future]
                try:
                    observations[agent_id] = future.result()
                except Exception as exc:
                    errors[agent_id] = str(exc)
        return observations, errors
