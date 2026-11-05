"""Drives OltClient against a scripted fake Telnet so the real login/session flow is exercised."""
import re

import pytest

from ispmon import olt


class FakeTelnet:
    instances = []
    fail_connect = 0

    def __init__(self, host, port=23, timeout=None):
        if FakeTelnet.fail_connect > 0:
            FakeTelnet.fail_connect -= 1
            raise OSError("timed out")
        self.buffer = b"Login:"
        self.commands = []
        self.closed = False
        FakeTelnet.instances.append(self)

    def write(self, data):
        if self.closed:
            raise OSError("closed")
        cmd = data.decode().strip()
        self.commands.append(cmd)
        self.buffer += self.respond(cmd)

    def respond(self, cmd):
        if cmd == "admin":
            return b"Password:"
        if cmd == "secret":
            return b"epon-olt>"
        if cmd == "show onu opm-diag":
            return FakeTelnet.opm + b"epon-olt(config-pon-0/1)# "
        if cmd == "show onu status":
            return FakeTelnet.status + b"epon-olt(config-pon-0/1)# "
        return b"epon-olt(config)# "

    def expect(self, patterns, timeout=None):
        best = None
        for index, pattern in enumerate(patterns):
            match = re.search(pattern, self.buffer)
            if match and (best is None or match.start() < best[1].start()):
                best = (index, match)
        if best is None:
            data, self.buffer = self.buffer, b""
            return -1, None, data
        index, match = best
        data, self.buffer = self.buffer[: match.end()], self.buffer[match.end():]
        return index, match, data

    def read_very_eager(self):
        data, self.buffer = self.buffer, b""
        return data

    def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch, fixture_text):
    FakeTelnet.instances, FakeTelnet.fail_connect = [], 0
    FakeTelnet.opm = fixture_text("olt11_opm.txt").encode()
    FakeTelnet.status = fixture_text("olt11_status.txt").encode()
    monkeypatch.setattr(olt.telnetlib, "Telnet", FakeTelnet)
    monkeypatch.setattr(olt.time, "sleep", lambda _s: None)
    return olt.OltClient("10.0.0.11", "admin", "secret", ("0/1",), cmd_wait_s=0)


def test_collect_parses_both_tables(client):
    optics, status = client.collect()
    assert len(optics) == 23 and len(status) == 27


def test_session_is_reused_between_cycles(client):
    client.collect()
    client.collect()
    assert len(FakeTelnet.instances) == 1


def test_dead_session_is_rebuilt(client):
    client.collect()
    FakeTelnet.instances[0].closed = True
    optics, status = client.collect()
    assert len(FakeTelnet.instances) == 2 and len(status) == 27


def test_connect_failure_is_retried_once(client):
    FakeTelnet.fail_connect = 1
    _, status = client.collect()
    assert len(status) == 27


def test_persistent_failure_raises(client):
    FakeTelnet.fail_connect = 5
    with pytest.raises(olt.OltError):
        client.collect()


def test_empty_status_table_is_an_error_not_an_outage(client):
    FakeTelnet.status = b"\n"
    with pytest.raises(olt.OltError):
        client.collect()
