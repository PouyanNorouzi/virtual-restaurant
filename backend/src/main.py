"""Backend entrypoint: loads config, sets up logging, and runs the MQTT
connection loop until interrupted (SIGINT/SIGTERM).
"""

import asyncio
import logging
import signal

from .config import Settings
from .logging_setup import configure_logging
from .mqtt.client import run_forever

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # broker_password from env
    configure_logging(settings.log_level)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info(
        "starting virtual restaurant backend",
        extra={"event": "startup", "num_tables": settings.num_tables},
    )
    await run_forever(settings, stop_event)
    logger.info("shutdown complete", extra={"event": "shutdown"})


if __name__ == "__main__":
    asyncio.run(main())
