"""Single source of truth for MQTT topic strings.

Topic layout:
    restaurant/table/{table_id}/order              (frontend -> backend)
    restaurant/table/{table_id}/food               (backend -> frontend)
    restaurant/table/{table_id}/order/rejected     (backend -> frontend)
    restaurant/seating/{session_id}/request        (frontend -> backend)
    restaurant/seating/{session_id}/vacate         (frontend -> backend, or
                                                     the broker itself via
                                                     that client's Last Will)
    restaurant/seating/{session_id}/status         (backend -> that session)
    restaurant/seating/occupancy                   (backend -> everyone, retained)

table_id is part of the order topics (not just the payload) so that
Mosquitto ACLs can scope the backend's own visibility; session_id is part
of the seating topics so Mosquitto's %c-pattern ACL can scope each customer
to its own seating topics - see mosquitto/acl.conf.
"""

ORDER_SUBSCRIBE_FILTER = "restaurant/table/+/order"
SEATING_REQUEST_SUBSCRIBE_FILTER = "restaurant/seating/+/request"
SEATING_VACATE_SUBSCRIBE_FILTER = "restaurant/seating/+/vacate"
OCCUPANCY_TOPIC = "restaurant/seating/occupancy"


def order_topic(table_id: int) -> str:
    return f"restaurant/table/{table_id}/order"


def food_topic(table_id: int) -> str:
    return f"restaurant/table/{table_id}/food"


def rejected_topic(table_id: int) -> str:
    return f"restaurant/table/{table_id}/order/rejected"


def seating_request_topic(session_id: str) -> str:
    return f"restaurant/seating/{session_id}/request"


def seating_vacate_topic(session_id: str) -> str:
    return f"restaurant/seating/{session_id}/vacate"


def seating_status_topic(session_id: str) -> str:
    return f"restaurant/seating/{session_id}/status"


def parse_table_id_from_order_topic(topic: str) -> int | None:
    """Extracts {table_id} from "restaurant/table/{table_id}/order", or None
    if the topic doesn't match that shape or the id isn't an integer.
    """
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "restaurant" or parts[1] != "table" or parts[3] != "order":
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def _parse_session_id_from_seating_topic(topic: str, last_segment: str) -> str | None:
    parts = topic.split("/")
    if (
        len(parts) != 4
        or parts[0] != "restaurant"
        or parts[1] != "seating"
        or parts[3] != last_segment
    ):
        return None
    session_id = parts[2]
    return session_id or None


def parse_session_id_from_seating_request_topic(topic: str) -> str | None:
    """Extracts {session_id} from "restaurant/seating/{session_id}/request"."""
    return _parse_session_id_from_seating_topic(topic, "request")


def parse_session_id_from_seating_vacate_topic(topic: str) -> str | None:
    """Extracts {session_id} from "restaurant/seating/{session_id}/vacate"."""
    return _parse_session_id_from_seating_topic(topic, "vacate")
