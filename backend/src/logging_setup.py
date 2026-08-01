"""Plain-text logging configuration.

Every log call site in this codebase passes extra={"event": ..., ...} so a
given order's log lines can be traced end to end by order_id. A bare
logging.Formatter only prints the fields named in its format string, so
those extras would otherwise be silently dropped; _ExtrasFormatter appends
them as "key=value" pairs instead of rendering them as JSON.
"""

import logging
import sys

# Every attribute a LogRecord carries by default (see logging.LogRecord.__init__).
# Anything on a record's __dict__ NOT in this set came from a call site's
# extra={...} and should be printed.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class _ExtrasFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Captured before super().format(), which mutates the record (sets
        # .message/.asctime as a side effect) - doing this after would count
        # those as call-site extras too.
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS
        }
        base = super().format(record)
        if not extras:
            return base
        rendered = " ".join(f"{key}={value!r}" for key, value in extras.items())
        return f"{base} | {rendered}"


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ExtrasFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
