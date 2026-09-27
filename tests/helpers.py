import asyncio
import contextlib
import json
import time
from pathlib import Path

from httpx import ASGITransport, AsyncClient

TESTS_DIR = Path(__file__).parent
PLAIN_CONFIG = TESTS_DIR / "config.json"
COOPERATIVE_CONFIG = TESTS_DIR / "config_cooperative.json"

FAST_STEPPING = {"rt_step_duration_s": 0.02, "rt_step_init_delay_s": 0.01}


def write_config(path, base=PLAIN_CONFIG, **overrides):
    config = json.loads(Path(base).read_text())
    config.update(overrides)
    Path(path).write_text(json.dumps(config))
    return str(path)


def client(app, ip="10.0.0.1"):
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 50000)), base_url="http://test"
    )


async def wait_until(condition, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the stepping loop"
        await asyncio.sleep(0.005)


async def stop_stepping(controller):
    controller.shutdown()
    main_loop = getattr(controller, "_main_loop", None)
    if main_loop is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await main_loop
