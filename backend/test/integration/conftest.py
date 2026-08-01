"""Spins up a real Mosquitto broker (via testcontainers) for integration
tests, configured with the same acl.conf/mosquitto.conf used in production,
plus a throwaway passwd file generated on the fly so tests don't depend on
mosquitto/passwd being present (which is gitignored / secrets-managed).
"""

import asyncio
import json
import pathlib
import subprocess

import aiomqtt
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

from src.mqtt.topics import seating_request_topic, seating_status_topic

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
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


@pytest.fixture(scope="session")
def mosquitto_broker(mosquitto_passwd_file):
    container = (
        DockerContainer("eclipse-mosquitto:2")
        .with_volume_mapping(
            str(BACKEND_DIR / "mosquitto" / "mosquitto.conf"),
            "/mosquitto/config/mosquitto.conf",
            "ro",
        )
        .with_volume_mapping(
            str(BACKEND_DIR / "mosquitto" / "acl.conf"), "/mosquitto/config/acl.conf", "ro"
        )
        .with_volume_mapping(str(mosquitto_passwd_file), "/mosquitto/config/passwd", "ro")
        .with_exposed_ports(1883, 9001)
    )
    with container:
        wait_for_logs(container, "mosquitto version .* running", timeout=30)
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(1883))
        yield {
            "host": host,
            "port": port,
            "password": TEST_PASSWORD,
            "container": container,
        }
