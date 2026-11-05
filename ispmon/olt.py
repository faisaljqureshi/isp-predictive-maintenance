"""V-SOL OLT client: one persistent Telnet session per OLT, scraping the CLI tables."""
from __future__ import annotations

import logging
import time
import warnings

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import telnetlib
except ModuleNotFoundError as exc:  # Python 3.13 removed telnetlib
    raise SystemExit("telnetlib is missing. Run: pip install telnetlib-313-and-up") from exc

from ispmon.parsers import OnuOptics, OnuStatus, parse_optics, parse_status

log = logging.getLogger("ispmon.olt")


class OltError(Exception):
    """The OLT did not behave as expected (timeout, rejected login, unparseable output)."""


class OltClient:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        pon_ports: tuple[str, ...],
        cmd_wait_s: float = 3.0,
        connect_timeout_s: float = 10.0,
    ) -> None:
        self.host = host
        self._user = user.encode()
        self._password = password.encode()
        self._pon_ports = pon_ports
        self._cmd_wait_s = cmd_wait_s
        self._connect_timeout_s = connect_timeout_s
        self._tn: telnetlib.Telnet | None = None

    # -- public API ---------------------------------------------------------------------------
    def collect(self) -> tuple[list[OnuOptics], list[OnuStatus]]:
        """Return (optics, status). Reuses the open session; rebuilds it once if it has gone bad."""
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                return self._read_tables(self._session())
            except (OSError, EOFError, OltError) as exc:
                last_error = exc
                log.warning("%s: attempt %d failed: %s", self.host, attempt, exc)
                self.close()
                if attempt == 1:
                    time.sleep(3)
        raise OltError(f"{self.host}: {last_error}") from last_error

    def close(self) -> None:
        tn, self._tn = self._tn, None
        if tn is None:
            return
        try:
            for _ in range(4):  # interface -> config -> enable -> user mode -> logout
                tn.write(b"exit\n")
                time.sleep(0.2)
        except (OSError, EOFError):
            pass
        try:
            tn.close()
        except OSError:
            pass

    # -- internals ----------------------------------------------------------------------------
    def _expect(self, tn: telnetlib.Telnet, patterns: list[bytes], timeout: float, what: str) -> tuple[int, bytes]:
        index, _match, data = tn.expect(patterns, timeout)
        if index == -1:
            raise OltError(f"timed out waiting for {what}")
        return index, data

    def _connect(self) -> telnetlib.Telnet:
        tn = telnetlib.Telnet(self.host, 23, self._connect_timeout_s)
        try:
            self._expect(tn, [b"Login:"], 10, "login prompt")
            tn.write(self._user + b"\n")
            self._expect(tn, [b"Password:"], 10, "password prompt")
            tn.write(self._password + b"\n")
            index, _ = self._expect(tn, [b">", b"Login:", b"(?i)incorrect|invalid|fail"], 10, "shell prompt")
            if index != 0:
                raise OltError("login rejected")
            tn.write(b"enable\n")
            index, _ = self._expect(tn, [b"Password:", b"#"], 10, "enable prompt")
            if index == 0:
                tn.write(self._password + b"\n")
                self._expect(tn, [b"#"], 10, "privileged prompt")
            for command in (b"terminal length 0", b"configure terminal"):
                tn.write(command + b"\n")
                self._expect(tn, [b"#"], 10, command.decode())
            return tn
        except BaseException:
            try:
                tn.close()
            except OSError:
                pass
            raise

    def _session(self) -> telnetlib.Telnet:
        if self._tn is not None:
            try:  # liveness check: a session the OLT closed or wedged fails here and gets rebuilt
                self._tn.read_very_eager()
                self._tn.write(b"\n")
                self._expect(self._tn, [b"#"], 5, "prompt")
                return self._tn
            except (OSError, EOFError, OltError):
                self.close()
        self._tn = self._connect()
        return self._tn

    def _run(self, tn: telnetlib.Telnet, command: str) -> str:
        tn.read_very_eager()  # drop stale bytes so output cannot be attributed to the wrong command
        tn.write(command.encode() + b"\n")
        time.sleep(self._cmd_wait_s)
        _, data = self._expect(tn, [b"#"], 30, f"output of '{command}'")
        return data.decode("ascii", errors="ignore")

    def _read_tables(self, tn: telnetlib.Telnet) -> tuple[list[OnuOptics], list[OnuStatus]]:
        optics: list[OnuOptics] = []
        status: list[OnuStatus] = []
        for port in self._pon_ports:
            tn.write(f"interface epon {port}\n".encode())
            self._expect(tn, [b"#"], 10, f"interface epon {port}")
            optics += parse_optics(self._run(tn, "show onu opm-diag"))
            status += parse_status(self._run(tn, "show onu status"))
            tn.write(b"exit\n")  # back to config mode, ready for the next cycle
            self._expect(tn, [b"#"], 10, "config prompt")
        if not status:
            raise OltError("no ONU rows parsed from 'show onu status'")
        return optics, status
