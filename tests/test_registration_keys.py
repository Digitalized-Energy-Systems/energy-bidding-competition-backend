import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from hackathon_backend.controller import ControlException, Controller
import hackathon_backend.interface as interface
from tests.helpers import PLAIN_CONFIG


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def keys_file(tmp_path):
    return str(tmp_path / "registration_keys.json")


@pytest.fixture
async def controller(monkeypatch, keys_file):
    controller = Controller(str(PLAIN_CONFIG), registration_keys_file=keys_file)
    controller.config = controller.config.model_copy(
        update={"issue_registration_keys": True}
    )
    monkeypatch.setattr(interface, "controller", controller)
    return controller


@pytest.fixture
def app(controller):
    app = FastAPI()
    app.include_router(interface.router)
    return app


def client(app, ip="10.0.0.1"):
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 50000)), base_url="http://test"
    )


async def request_key(app, name, ip="10.0.0.1"):
    async with client(app, ip) as ac:
        return await ac.post("/hackathon/key", params={"name": name})


async def register(app, key):
    async with client(app) as ac:
        return await ac.post("/hackathon/register", params={"participant_id": key})


@pytest.mark.anyio
async def test_issued_key_registers_participant_under_its_name(app, controller):
    response = await request_key(app, "Alice")
    assert response.status_code == 200
    key = response.json()["key"]
    assert len(key) >= 20

    response = await register(app, key)
    assert response.status_code == 200
    actor_id = response.json()["actor_id"]

    assert controller.participant_display_names() == {actor_id: "Alice"}
    async with client(app) as ac:
        participant_map = (await ac.get("/ui/participant_map")).json()
    assert participant_map == {actor_id: "Alice"}
    assert key not in str(participant_map)

    # the key authenticates the actor
    async with client(app) as ac:
        response = await ac.get(
            "/units/information", params={"actor_id": actor_id, "key": key}
        )
    assert response.status_code == 200

    # test mode: registering again returns the same actor
    response = await register(app, key)
    assert response.json()["actor_id"] == actor_id
    assert len(controller.registered) == 1


@pytest.mark.anyio
async def test_one_key_per_address(app):
    assert (await request_key(app, "Alice", ip="10.0.0.1")).status_code == 200
    assert (await request_key(app, "Bob", ip="10.0.0.1")).status_code == 409
    assert (await request_key(app, "Bob", ip="10.0.0.2")).status_code == 200


@pytest.mark.anyio
async def test_max_keys_per_ip_raises_the_limit(app, controller):
    controller.config.max_keys_per_ip = 2
    assert (await request_key(app, "Alice")).status_code == 200
    assert (await request_key(app, "Bob")).status_code == 200
    assert (await request_key(app, "Carol")).status_code == 409


@pytest.mark.anyio
async def test_names_are_unique(app, controller):
    controller.config.participants = ["Dave42"]
    assert (await request_key(app, "Alice", ip="10.0.0.1")).status_code == 200
    assert (await request_key(app, " alice ", ip="10.0.0.2")).status_code == 409
    assert (await request_key(app, "DAVE", ip="10.0.0.3")).status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["", "   ", "x" * 33, "tab\tname"])
async def test_invalid_names_are_refused(app, name):
    assert (await request_key(app, name)).status_code == 400


@pytest.mark.anyio
async def test_no_keys_while_disabled(app, controller):
    controller.config.issue_registration_keys = False
    assert (await request_key(app, "Alice")).status_code == 403


@pytest.mark.anyio
async def test_no_keys_after_registration_closed(app, controller):
    controller.registration_open = False
    assert (await request_key(app, "Alice")).status_code == 405


@pytest.mark.anyio
async def test_unknown_key_is_refused(app):
    assert (await register(app, "not-a-key")).status_code == 403


@pytest.mark.anyio
async def test_configured_participants_still_register(app):
    assert (await register(app, "TestA")).status_code == 200


@pytest.mark.anyio
async def test_keys_survive_a_restart(controller, keys_file):
    key = await controller.issue_registration_key("Alice", "10.0.0.1")

    restarted = Controller(str(PLAIN_CONFIG), registration_keys_file=keys_file)
    restarted.config.issue_registration_keys = True

    actor_id, _ = await restarted.register_actor(key)
    assert restarted.actor_to_participant[actor_id][0:-2] == "Alice"
    assert restarted.participant_display_names() == {actor_id: "Alice"}
    assert len(await restarted.read_units(actor_id, key)) == 3
    with pytest.raises(ControlException) as e:
        await restarted.issue_registration_key("Bob", "10.0.0.1")
    assert e.value.code == 409
