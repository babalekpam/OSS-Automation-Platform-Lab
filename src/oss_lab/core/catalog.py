"""Service catalog (TMF633): service specifications defined once, consumed by the
orchestrator, activation and assurance."""

from __future__ import annotations

import ipaddress
from typing import Any, Literal

from pydantic import BaseModel, Field

from .errors import Conflict, NotFound, ValidationFailed


class Characteristic(BaseModel):
    name: str
    type: Literal["string", "integer", "boolean", "ipv4"] = "string"
    required: bool = True
    default: Any = None
    immutable: bool = False


class ServiceSpecification(BaseModel):
    id: str
    name: str
    version: str = "1.0"
    description: str = ""
    characteristics: list[Characteristic]
    # Jinja templates. ``config_key`` -> device config path, ``template`` -> JSON value.
    config_key: str
    template: str
    # Decomposition: ordered resource actions the orchestrator performs on "add".
    decomposition: list[str] = Field(
        default_factory=lambda: ["reserve", "activate", "enroll_assurance"]
    )


L3_INTERFACE = ServiceSpecification(
    id="l3-interface",
    name="L3 Interface Service",
    description="Routed interface with an IPv4 address on a managed device.",
    characteristics=[
        Characteristic(name="device", immutable=True),
        Characteristic(name="interface", immutable=True),
        Characteristic(name="ipv4", type="ipv4"),
        Characteristic(name="prefix_length", type="integer", required=False, default=31),
        Characteristic(name="mtu", type="integer", required=False, default=9000),
        Characteristic(name="description", required=False, default=""),
    ],
    config_key="interfaces/{{ interface }}",
    template=(
        '{"description": {{ description|tojson }}, "enabled": {{ enabled|tojson }}, '
        '"mtu": {{ mtu }}, "ipv4": {"address": {{ ipv4|tojson }}, '
        '"prefix_length": {{ prefix_length }}}}'
    ),
)

LOOPBACK = ServiceSpecification(
    id="loopback",
    name="Loopback Service",
    description="Host-route loopback used for management and routing identity.",
    characteristics=[
        Characteristic(name="device", immutable=True),
        Characteristic(name="interface", required=False, default="lo0", immutable=True),
        Characteristic(name="ipv4", type="ipv4"),
        Characteristic(name="description", required=False, default="loopback"),
    ],
    config_key="interfaces/{{ interface }}",
    template=(
        '{"description": {{ description|tojson }}, "enabled": {{ enabled|tojson }}, '
        '"mtu": 1500, "ipv4": {"address": {{ ipv4|tojson }}, "prefix_length": 32}}'
    ),
)


def _coerce(char: Characteristic, value: Any) -> Any:
    try:
        if char.type == "integer":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if char.type == "boolean":
            if not isinstance(value, bool):
                raise ValueError
            return value
        if char.type == "ipv4":
            return str(ipaddress.IPv4Address(value))
        return str(value)
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise ValidationFailed(
            f"characteristic '{char.name}' must be of type {char.type}, got {value!r}"
        ) from None


class Catalog:
    def __init__(self, seed: bool = True) -> None:
        self._specs: dict[str, ServiceSpecification] = {}
        if seed:
            for spec in (L3_INTERFACE, LOOPBACK):
                self.add(spec.model_copy(deep=True))

    def add(self, spec: ServiceSpecification) -> ServiceSpecification:
        if spec.id in self._specs:
            raise Conflict(f"service specification '{spec.id}' already exists")
        self._specs[spec.id] = spec
        return spec

    def get(self, spec_id: str) -> ServiceSpecification:
        try:
            return self._specs[spec_id]
        except KeyError:
            raise NotFound(f"service specification '{spec_id}' not found") from None

    def list(self) -> list[ServiceSpecification]:
        return list(self._specs.values())

    def validate_characteristics(
        self, spec: ServiceSpecification, values: dict[str, Any]
    ) -> dict[str, Any]:
        known = {c.name: c for c in spec.characteristics}
        unknown = sorted(set(values) - set(known))
        if unknown:
            raise ValidationFailed(f"unknown characteristics for '{spec.id}': {unknown}")
        out: dict[str, Any] = {}
        for name, char in known.items():
            if name in values and values[name] is not None:
                out[name] = _coerce(char, values[name])
            elif char.default is not None:
                out[name] = char.default
            elif char.required:
                raise ValidationFailed(f"missing required characteristic '{name}'")
        return out
