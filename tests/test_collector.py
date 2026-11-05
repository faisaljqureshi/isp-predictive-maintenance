"""Runs real collector cycles with the network clients faked, against a real SQLite file."""
from datetime import datetime, timedelta

import pytest

from ispmon import collector, db
from ispmon.config import Settings, Thresholds
from ispmon.mikrotik import MikroTikSample
from ispmon.olt import OltError
from ispmon.parsers import parse_optics, parse_status


class FakeMikroTik:
    fail = False

    def __init__(self, *a, **k): pass

    def collect(self):
        if FakeMikroTik.fail:
            raise RuntimeError("connection reset")
        return MikroTikSample(cpu_load=11, free_memory_mb=350.0,
                              interfaces=[("ether1", "uplink", 1, 1000, 2000, 0, 0), ("ether2", "", 0, 5, 6, 0, 0)],
                              logs=[("10:00:00", "system,info", "login failure for user x from 1.2.3.4 via ssh")])

    def close(self): pass


class FakeOlt:
    tables: dict = {}
    failing: set = set()

    def __init__(self, host, *a, **k):
        self.host = host

    def collect(self):
        if self.host in FakeOlt.failing:
            raise OltError("timed out waiting for login prompt")
        opm, status = FakeOlt.tables[self.host]
        return parse_optics(opm), parse_status(status)

    def close(self): pass


def settings(tmp_path):
    return Settings(db_path=tmp_path / "t.db", log_dir=tmp_path, log_level="INFO", interval_s=60,
                    mk_host="mk", mk_port=8728, mk_user="u", mk_password="p", mk_use_ssl=False,
                    olt_hosts=("olt-a", "olt-b"), olt_user="u", olt_password="p", pon_ports=("0/1",),
                    olt_cmd_wait_s=0, olt_connect_timeout_s=1, retention_interface_days=90, retention_log_days=180,
                    thresholds=Thresholds(-24, -25, 80, 180, 5, 120))


@pytest.fixture
def make(tmp_path, monkeypatch, fixture_text):
    FakeMikroTik.fail, FakeOlt.failing = False, set()
    FakeOlt.tables = {"olt-a": (fixture_text("olt11_opm.txt"), fixture_text("olt11_status.txt")),
                      "olt-b": (fixture_text("olt12_opm.txt"), fixture_text("olt12_status.txt"))}
    monkeypatch.setattr(collector, "MikroTikClient", FakeMikroTik)
    monkeypatch.setattr(collector, "OltClient", FakeOlt)
    return lambda: collector.Collector(settings(tmp_path))


def count(c, table, where="1=1"):
    return c.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def test_one_cycle_writes_everything(make):
    c = make()
    summary = c.cycle()
    assert summary["olts"] == {"olt-a": 27, "olt-b": 23} and summary["events"] == 0
    assert count(c, "olt_optical_stats") == 23 + 20
    assert count(c, "onu_status") == 27 + 23
    assert count(c, "interface_stats", "running = 0") == 1
    assert count(c, "security_logs") == 1
    assert count(c, "device_heartbeat", "ok = 1") == 3
    assert c.conn.execute("SELECT mikrotik_ok, olts_ok, onus_logged FROM collector_heartbeat").fetchone() == (1, 2, 50)


def test_state_change_creates_an_event(make):
    c = make()
    c.cycle()
    c.previous[("olt-a", "EPON0/1:2")] = ("offline", "x")  # pretend it was offline: now it reads online
    c.last_seen["olt-a"] = datetime.now()
    c.cycle()
    assert c.conn.execute("SELECT event, after_gap FROM onu_events").fetchall() == [("came_online", 0)]


def test_failed_olt_never_marks_onus_offline_and_is_reported(make):
    c = make()
    c.cycle()
    FakeOlt.failing = {"olt-b"}
    summary = c.cycle()
    assert summary["olts"]["olt-b"].startswith("FAILED")
    assert count(c, "onu_events") == 0
    assert count(c, "onu_status", "olt_ip = 'olt-b'") == 23          # only the first cycle
    assert c.conn.execute("SELECT olts_ok FROM collector_heartbeat ORDER BY timestamp DESC, rowid DESC LIMIT 1").fetchone()[0] == 1
    assert count(c, "device_heartbeat", "ok = 0 AND device = 'olt olt-b'") == 1


def test_router_failure_does_not_block_olt_collection(make):
    c = make()
    FakeMikroTik.fail = True
    c.cycle()
    assert count(c, "system_health") == 0 and count(c, "olt_optical_stats") == 43
    assert c.conn.execute("SELECT mikrotik_ok FROM collector_heartbeat").fetchone()[0] == 0


def test_events_after_a_long_gap_are_flagged(make):
    c = make()
    c.cycle()
    c.last_seen["olt-a"] = datetime.now() - timedelta(hours=3)
    c.previous[("olt-a", "EPON0/1:1")] = ("offline", "x")
    c.cycle()
    assert c.conn.execute("SELECT after_gap FROM onu_events WHERE olt_ip = 'olt-a'").fetchone()[0] == 1


def test_restart_resumes_from_stored_state(make):
    c = make()
    c.cycle()
    c.conn.close()
    again = make()
    assert again.previous[("olt-a", "EPON0/1:7")][0] == "offline"
    assert again.last_seen["olt-a"] <= datetime.now()
