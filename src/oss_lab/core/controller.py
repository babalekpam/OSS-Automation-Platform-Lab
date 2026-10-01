"""Southbound controller and device simulator.

``SimDevice`` models a NETCONF/gNMI-style target with candidate/commit semantics.
``Controller`` pushes transactional changes, verifies by read-back, and keeps its
own view of intended state so drift can be detected independently of the device."""

from __future__ import annotations

import copy
import ipaddress
from typing import Any

from .errors import DeviceError, NotFound, TransientError

MANAGED_PREFIX = "interfaces/"


class SimDevice:
    def __init__(self, device_id: str, interfaces: list[str], os: str = "srlinux") -> None:
        self.id = device_id
        self.os = os
        self.interfaces = set(interfaces)
        self.config: dict[str, Any] = {"system/hostname": {"name": device_id}}
        self._permanent_failures = 0
        self._transient_failures = 0

    def inject_fault(self, mode: str, count: int = 1) -> None:
        if mode == "permanent":
            self._permanent_failures = count
        elif mode == "transient":
            self._transient_failures = count
        else:
            raise ValueError(f"unknown fault mode '{mode}'")

    def _validate(self, key: str, value: dict[str, Any]) -> None:
        if key.startswith(MANAGED_PREFIX):
            name = key[len(MANAGED_PREFIX) :]
            if name not in self.interfaces:
                raise DeviceError(f"{self.id}: unknown interface '{name}'")
            ipv4 = value.get("ipv4", {})
            try:
                ipaddress.IPv4Address(ipv4["address"])
            except (KeyError, ValueError):
                raise DeviceError(f"{self.id}: invalid ipv4 address in {key}") from None
            if not 576 <= int(value.get("mtu", 1500)) <= 9216:
                raise DeviceError(f"{self.id}: mtu out of range in {key}")

    def commit(self, changes: dict[str, dict[str, Any] | None]) -> None:
        """Atomically apply ``changes``; a value of ``None`` deletes the key."""
        if self._transient_failures > 0:
            self._transient_failures -= 1
            raise TransientError(f"{self.id}: session temporarily unavailable")
        if self._permanent_failures > 0:
            self._permanent_failures -= 1
            raise DeviceError(f"{self.id}: commit rejected (injected fault)")
        candidate = copy.deepcopy(self.config)
        for key, value in changes.items():
            if value is None:
                candidate.pop(key, None)
            else:
                self._validate(key, value)
                candidate[key] = copy.deepcopy(value)
        self.config = candidate


class Controller:
    def __init__(self) -> None:
        self.devices: dict[str, SimDevice] = {}
        self._view: dict[str, dict[str, Any]] = {}

    def register_device(self, device_id: str, interfaces: list[str], os: str = "srlinux") -> None:
        self.devices[device_id] = SimDevice(device_id, interfaces, os)
        self._view[device_id] = {}

    def device(self, device_id: str) -> SimDevice:
        try:
            return self.devices[device_id]
        except KeyError:
            raise NotFound(f"device '{device_id}' not registered with controller") from None

    def push(self, device_id: str, key: str, value: dict[str, Any]) -> None:
        dev = self.device(device_id)
        dev.commit({key: value})
        if dev.config.get(key) != value:  # verify by read-back
            raise DeviceError(f"{device_id}: verification failed for {key}")
        self._view[device_id][key] = copy.deepcopy(value)

    def remove(self, device_id: str, key: str) -> None:
        dev = self.device(device_id)
        dev.commit({key: None})
        if key in dev.config:
            raise DeviceError(f"{device_id}: verification failed removing {key}")
        self._view[device_id].pop(key, None)

    def view(self, device_id: str) -> dict[str, Any]:
        self.device(device_id)
        return copy.deepcopy(self._view[device_id])

    def actual(self, device_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.device(device_id).config)

    # -- migration support --------------------------------------------------
    def export_state(self) -> dict[str, Any]:
        return {
            "devices": {
                d.id: {"os": d.os, "interfaces": sorted(d.interfaces), "config": d.config}
                for d in self.devices.values()
            },
            "view": copy.deepcopy(self._view),
        }

    def import_state(self, state: dict[str, Any]) -> None:
        self.devices, self._view = {}, {}
        for dev_id, info in state["devices"].items():
            self.register_device(dev_id, info["interfaces"], info["os"])
            self.devices[dev_id].config = copy.deepcopy(info["config"])
        self._view = copy.deepcopy(state["view"])
