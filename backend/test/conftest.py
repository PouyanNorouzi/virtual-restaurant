"""Spins up a real Mosquitto broker (via testcontainers) for integration
tests, configured with the same acl.conf/mosquitto.conf used in production,
plus a throwaway passwd file generated on the fly so tests don't depend on
mosquitto/passwd being present (which is gitignored / secrets-managed).
"""

import asyncio
import json
import pathlib
import socket
import subprocess

import aiomqtt
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from src.mqtt.topics import seating_request_topic, seating_status_topic

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
TEST_PASSWORD = "test-password"
# Only two accounts now: the backend, and the single shared "customer" role
# every browser client authenticates as. Per-session isolation for seating
# topics comes from %c-pattern ACLs (see mosquitto/acl.conf), not from a
# separate credential per table/session - see backend/README.md Security.
TEST_USERS = ["backend", "customer"]


async def request_seat_and_wait_assigned(client: aiomqtt.Client, session_id: str) -> int:
    """Test helper: requests a seat for session_id over `client` (whose MQTT
    Client Identifier must equal session_id, per protocol convention - see
    topics.py) and waits for the backend's "assigned" status, returning the
    table_id it was assigned. Subscribes before publishing to avoid missing
    the reply.
    """
    await client.subscribe(seating_status_topic(session_id), qos=1)
    await client.publish(seating_request_topic(session_id), payload=b"{}", qos=1)
    message = await asyncio.wait_for(anext(aiter(client.messages)), timeout=5)
    payload = json.loads(message.payload)
    assert payload["state"] == "assigned", payload
    return payload["table_id"]


@pytest.fixture(scope="session")
def mosquitto_passwd_file(tmp_path_factory):
    passwd_dir = tmp_path_factory.mktemp("mosquitto-passwd")
    passwd_path = passwd_dir / "passwd"
    passwd_path.touch()

    for user in TEST_USERS:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{passwd_path}:/mosquitto/config/passwd",
                "eclipse-mosquitto:2",
                "mosquitto_passwd",
                "-b",
                "/mosquitto/config/passwd",
                user,
                TEST_PASSWORD,
            ],
            check=True,
            capture_output=True,
        )
    return passwd_path


def _new_mosquitto_container(passwd_file: pathlib.Path) -> DockerContainer:
    return (
        DockerContainer("eclipse-mosquitto:2")
        .with_volume_mapping(
            str(BACKEND_DIR / "mosquitto" / "mosquitto.conf"),
            "/mosquitto/config/mosquitto.conf",
            "ro",
        )
        .with_volume_mapping(
            str(BACKEND_DIR / "mosquitto" / "acl.conf"), "/mosquitto/config/acl.conf", "ro"
        )
        .with_volume_mapping(str(passwd_file), "/mosquitto/config/passwd", "ro")
        .with_exposed_ports(1883, 9001)
        .waiting_for(
            LogMessageWaitStrategy(r"mosquitto version .* running").with_startup_timeout(30)
        )
    )


def _free_tcp_port() -> int:
    """Picks a currently-unused host port to pin the restartable broker to
    - see restartable_mosquitto_broker for why a fixed port matters here.
    Racy in principle (the port could be grabbed between this returning and
    the container binding it), but that's the same trick testcontainers'
    own dynamic port assignment relies on, and it's the container binding
    it moments later, not another test process.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def mosquitto_broker(mosquitto_passwd_file):
    container = _new_mosquitto_container(mosquitto_passwd_file)
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(1883))
        yield {
            "host": host,
            "port": port,
            "password": TEST_PASSWORD,
            "container": container,
        }


@pytest.fixture
def restartable_mosquitto_broker(mosquitto_passwd_file):
    """A dedicated, function-scoped broker - separate from the session-scoped
    `mosquitto_broker` every other test shares. Only for tests that need to
    stop/restart/kill the broker itself: if a restart doesn't come back
    cleanly, that must not take down every other test sharing one instance.

    Binds 1883 to a host port picked up front, rather than letting Docker
    assign one on container start (like `_new_mosquitto_container` does for
    everyone else): confirmed against this project's Docker daemon that a
    dynamically-assigned port mapping gets silently reallocated across a
    `docker restart` (e.g. 32833 -> 32834), while a pinned mapping doesn't
    move. Since this fixture exists specifically to test surviving a broker
    restart, a moving port would defeat the point - the backend would have
    no way to know the broker moved and correctly stay disconnected.
    """
    container = (
        _new_mosquitto_container(mosquitto_passwd_file)
        .with_bind_ports(1883, _free_tcp_port())
    )
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(1883))
        yield {
            "host": host,
            "port": port,
            "password": TEST_PASSWORD,
            "container": container,
        }
