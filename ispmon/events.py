"""ONU state-change detection. These events are the labelled failures for the ML phase."""
from __future__ import annotations

from collections.abc import Iterable

from ispmon.parsers import OnuStatus

State = tuple[str, str]  # (status, last_reg_time)
EventRow = tuple[str, str, str, str, str, int]  # timestamp, olt_ip, onu_id, event, dereg_reason, after_gap


def detect_events(
    previous: dict[tuple[str, str], State],
    olt_ip: str,
    rows: Iterable[OnuStatus],
    timestamp: str,
    after_gap: bool,
) -> list[EventRow]:
    """Compare this poll with the last known state; update `previous` in place.

    The OLT clock is unreliable, so last_reg_time is only compared for equality, never read as a time.
    `after_gap` marks events found after the collector was blind for a while: the change is real but
    its timestamp is only an upper bound.
    """
    events: list[EventRow] = []
    for row in rows:
        key = (olt_ip, row.onu_id)
        new_state = (row.status, row.last_reg_time)
        old_state = previous.get(key)
        if old_state is not None:
            event = None
            if old_state[0] == "online" and new_state[0] == "offline":
                event = "went_offline"
            elif old_state[0] == "offline" and new_state[0] == "online":
                event = "came_online"
            elif new_state[0] == "online" and old_state[1] != new_state[1]:
                event = "reregistered"  # dropped and came back between two polls
            if event:
                events.append((timestamp, olt_ip, row.onu_id, event, row.last_dereg_reason, int(after_gap)))
        previous[key] = new_state
    return events
