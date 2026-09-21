"""Run a NetForge controller: python -m controller --port 8080."""

from __future__ import annotations

import argparse

from controller.config import ControllerConfig
from controller.dispatch import AgentDispatcher
from controller.http_server import serve
from controller.scheduler import MonitorScheduler
from controller.service import ControllerService
from controller.store import ControllerStore


def main() -> None:
    parser = argparse.ArgumentParser(description="NetForge controller")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="run the background scheduler that fires due service diagnoses",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args()
    config = ControllerConfig.from_env()
    service = ControllerService(ControllerStore(config.database_path), AgentDispatcher(config.agent_token))
    if args.monitor:
        MonitorScheduler(service, poll_interval=args.poll_interval).start()
    serve(service, config.controller_token, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
