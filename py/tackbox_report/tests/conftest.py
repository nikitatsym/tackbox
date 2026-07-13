from __future__ import annotations

import logging

import pytest

import tackbox_report as report

_FAKE_DSN = "https://public@example.invalid/1"


class ListHandler(logging.Handler):
    """Records emitted LogRecords so tests can assert the log-before-drop line."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def log() -> logging.Logger:
    lg = logging.getLogger("tackbox.report.test")
    lg.handlers.clear()
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    lg.addHandler(ListHandler())
    return lg


def records(lg: logging.Logger) -> list[logging.LogRecord]:
    return lg.handlers[0].records  # type: ignore[attr-defined]


@pytest.fixture
def events(log: logging.Logger):
    """Init capture against a fake DSN with a before_send that records every
    event and drops it (returns None), so nothing hits the network."""
    captured: list[dict] = []

    def before_send(event, hint):
        captured.append(event)
        return None

    report._reset_for_test()
    report.init(
        _FAKE_DSN,
        release="test",
        environment="test",
        logger=log,
        before_send=before_send,
    )
    yield captured
    report.flush(0.1)
    report._reset_for_test()
