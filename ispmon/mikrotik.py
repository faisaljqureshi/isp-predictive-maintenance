"""MikroTik RouterOS API client."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import routeros_api

log = logging.getLogger("ispmon.mikrotik")


@dataclass
class MikroTikSample:
    cpu_load: int
    free_memory_mb: float
    # (name, comment, running, rx_byte, tx_byte, rx_drop, tx_drop)
    interfaces: list[tuple] = field(default_factory=list)
    # (router_time, topics, message)
    logs: list[tuple] = field(default_factory=list)


class MikroTikClient:
    def __init__(self, host: str, user: str, password: str, port: int = 8728, use_ssl: bool = False) -> None:
        self.host = host
        self._user, self._password, self._port, self._use_ssl = user, password, port, use_ssl
        self._pool: routeros_api.RouterOsApiPool | None = None
        self._api = None

    def _handle(self):
        if self._api is None:
            self._pool = routeros_api.RouterOsApiPool(
                self.host,
                username=self._user,
                password=self._password,
                port=self._port,
                use_ssl=self._use_ssl,
                ssl_verify=False,  # router certificates are normally self-signed
                ssl_verify_hostname=False,
                plaintext_login=True,
            )
            self._api = self._pool.get_api()
        return self._api

    def close(self) -> None:
        pool, self._pool, self._api = self._pool, None, None
        if pool is not None:
            try:
                pool.disconnect()
            except Exception:  # noqa: BLE001 - closing a dead connection may raise anything
                pass

    def collect(self) -> MikroTikSample:
        try:
            api = self._handle()
            resource = api.get_resource("/system/resource").get()[0]
            sample = MikroTikSample(
                cpu_load=int(resource.get("cpu-load", 0)),
                free_memory_mb=int(resource.get("free-memory", 0)) / 1048576,
            )
            for item in api.get_resource("/interface").get():
                sample.interfaces.append(
                    (
                        item.get("name", "?"),
                        item.get("comment", ""),
                        1 if item.get("running") == "true" else 0,
                        int(item.get("rx-byte", 0)),
                        int(item.get("tx-byte", 0)),
                        int(item.get("rx-drop", 0)),
                        int(item.get("tx-drop", 0)),
                    )
                )
            for entry in api.get_resource("/log").get():
                sample.logs.append((entry.get("time"), entry.get("topics"), entry.get("message")))
            return sample
        except Exception:
            self.close()  # force a clean reconnect next cycle
            raise
