"""Run a NetForge agent: python -m agent --port 8081."""

from __future__ import annotations

import argparse

from agent.config import AgentConfig
from agent.http_server import serve
from agent.service import AgentService


def main() -> None:
    parser = argparse.ArgumentParser(description="NetForge remote agent")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address; pass 0.0.0.0 only behind TLS or a trusted network boundary",
    )
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--certfile", help="PEM certificate; enables TLS for the probe API")
    parser.add_argument("--keyfile", help="PEM private key (defaults to --certfile)")
    args = parser.parse_args()
    serve(
        AgentService(AgentConfig.from_env()),
        host=args.host,
        port=args.port,
        certfile=args.certfile,
        keyfile=args.keyfile,
    )


if __name__ == "__main__":
    main()
