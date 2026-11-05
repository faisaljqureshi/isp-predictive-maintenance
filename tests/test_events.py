from ispmon.events import detect_events
from ispmon.parsers import OnuStatus


def onu(onu_id="EPON0/1:1", status="online", reg="2044/06/04 08:00:44", reason="N/A"):
    return OnuStatus(onu_id, status, "02:00:00:00:00:01", 300, 200, reg, "N/A", reason, 100)


def test_first_poll_creates_no_events():
    prev = {}
    assert detect_events(prev, "olt", [onu()], "t0", False) == []
    assert prev[("olt", "EPON0/1:1")] == ("online", "2044/06/04 08:00:44")


def test_offline_then_online():
    prev = {("olt", "EPON0/1:1"): ("online", "r1")}
    down = detect_events(prev, "olt", [onu(status="offline", reg="r1", reason="Wire Down")], "t1", False)
    assert down == [("t1", "olt", "EPON0/1:1", "went_offline", "Wire Down", 0)]
    up = detect_events(prev, "olt", [onu(status="online", reg="r2", reason="Wire Down")], "t2", False)
    assert up[0][3] == "came_online"


def test_reregistered_between_polls():
    prev = {("olt", "EPON0/1:1"): ("online", "r1")}
    events = detect_events(prev, "olt", [onu(reg="r2", reason="Power Off")], "t1", False)
    assert [e[3] for e in events] == ["reregistered"]


def test_no_change_no_event_and_gap_flag():
    prev = {("olt", "EPON0/1:1"): ("online", "r1")}
    assert detect_events(prev, "olt", [onu(reg="r1")], "t1", False) == []
    events = detect_events(prev, "olt", [onu(status="offline", reg="r1")], "t2", True)
    assert events[0][5] == 1
