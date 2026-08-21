"""Structured (JSON) logging outside dev, human-readable inside it.

`logging.basicConfig(level=logging.INFO)`'s plain string-interpolated
output is fine to read directly in a terminal (which is all `dev` ever
needs), but painful the moment logs actually go to a real aggregator
(CloudWatch, Loki, Datadog, etc.) in staging/prod -- those tools all want
one JSON object per line, not a format string they have to regex apart.
Hand-rolled here rather than adding a dependency (structlog /
python-json-logger) for what's a genuinely small amount of logic -- one
`Formatter` subclass.
"""
from __future__ import annotations

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(environment: str, level: int = logging.INFO) -> None:
    """Call once, at import time, in place of `logging.basicConfig(...)`."""
    handler = logging.StreamHandler(sys.stdout)
    if environment == "dev":
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
