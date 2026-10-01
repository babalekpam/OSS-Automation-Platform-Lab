"""Composition root: wires the platform components into one stack at a given release."""

from __future__ import annotations

from typing import Any

from .activation import Activation
from .assurance import Assurance
from .catalog import Catalog
from .controller import Controller
from .events import EventBus
from .inventory import Inventory
from .orchestrator import Orchestrator
from .reconciliation import reconcile

SEED_DEVICES = {
    "r1": ["ethernet-1/1", "ethernet-1/2", "ethernet-1/3", "ethernet-1/4", "lo0"],
    "r2": ["ethernet-1/1", "ethernet-1/2", "ethernet-1/3", "ethernet-1/4", "lo0"],
    "r3": ["ethernet-1/1", "ethernet-1/2", "ethernet-1/3", "ethernet-1/4", "lo0"],
}


class Stack:
    def __init__(self, version: str = "n", seed: bool = True) -> None:
        self.version, self._seed = version, seed
        self._build()

    def _build(self) -> None:
        self.bus = EventBus()
        self.catalog = Catalog(seed=self._seed)
        self.inventory = Inventory()
        self.controller = Controller()
        self.activation = Activation(self.controller)
        self.assurance = Assurance()
        self.orchestrator = Orchestrator(
            self.catalog, self.inventory, self.activation, self.assurance, self.bus
        )
        if self._seed:
            for dev_id, ifaces in SEED_DEVICES.items():
                self.inventory.add_device(dev_id, ifaces)
                self.controller.register_device(dev_id, ifaces)

    def reset(self) -> None:
        self._build()

    def reconcile(self) -> dict[str, Any]:
        return reconcile(self.catalog, self.inventory, self.controller, self.assurance)

    def export_state(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "inventory": self.inventory.export_state(),
            "controller": self.controller.export_state(),
            "assurance": {k: list(v) for k, v in self.assurance.enrolled.items()},
        }

    def import_state(self, state: dict[str, Any]) -> None:
        self.inventory.import_state(state["inventory"])
        self.controller.import_state(state["controller"])
        self.assurance.enrolled = {k: list(v) for k, v in state["assurance"].items()}
