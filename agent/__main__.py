"""Run a NetForge agent: python -m agent --port 8081."""

from __future__ import annotations

import argparse

from agent.config import AgentConfig
from agent.http_server import serve
from agent.service import AgentService


def main() -> None:
    parser = argparse.ArgumentParser(description="NetForge remote agent")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    serve(AgentService(AgentConfig.from_env()), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
