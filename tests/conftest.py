import pytest
from fastapi import FastAPI

import hackathon_backend.interface as interface
from hackathon_backend.controller import Controller
from hackathon_backend.general_demand import create_general_demand
from tests.helpers import PLAIN_CONFIG, stop_stepping, write_config


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    # the after-step hooks write app_state.json and results/ into the cwd
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
async def api(workdir, monkeypatch):
    """Factory for a fresh controller behind the REST interface, with the
    persistence and score hooks attached and its config in workdir/config.json
    (where /admin/load expects it). Every stepping loop is stopped on
    teardown, also one started by /admin/load."""
    shared_controller = interface.controller
    created = []

    def make(base=PLAIN_CONFIG, **overrides):
        config_file = write_config(workdir / "config.json", base, **overrides)
        controller = Controller(
            config_file, registration_keys_file=str(workdir / "registration_keys.json")
        )
        controller.general_demand = create_general_demand("gd0")
        interface.add_after_step_hooks(controller)
        monkeypatch.setattr(interface, "controller", controller)
        created.append(controller)
        app = FastAPI()
        app.include_router(interface.router)
        return app, controller

    yield make

    for controller in {interface.controller, *created} - {shared_controller}:
        await stop_stepping(controller)
