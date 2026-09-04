"""Driver registry — discovers builtin and pip-installed drivers."""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ionemo_drivers.base import BaseDriver

logger = logging.getLogger(__name__)

# Maps driver name → driver class
DRIVER_REGISTRY: dict[str, type[BaseDriver]] = {}


def _load_builtin_drivers() -> None:
    """Import all builtin driver modules to trigger registration."""
    builtin_modules = [
        "ionemo_drivers.grid.homewizard_p1",
        "ionemo_drivers.pv.aurora_rs485",
        "ionemo_drivers.ev.alfen_eve",
        "ionemo_drivers.ac.daikin_brp",
    ]
    for module_path in builtin_modules:
        try:
            importlib.import_module(module_path)
        except ImportError as e:
            logger.warning("Could not load builtin driver %s: %s", module_path, e)


def _load_external_drivers() -> None:
    """Discover pip-installed driver plugins via entry points."""
    discovered = entry_points(group="ionemo.drivers")
    for ep in discovered:
        try:
            driver_class = ep.load()
            DRIVER_REGISTRY[ep.name] = driver_class
            logger.info("Loaded external driver: %s", ep.name)
        except Exception as e:
            logger.warning("Failed to load external driver %s: %s", ep.name, e)


def register_driver(name: str, driver_class: type[BaseDriver]) -> None:
    """Register a driver class in the global registry."""
    DRIVER_REGISTRY[name] = driver_class


def get_driver(name: str) -> type[BaseDriver] | None:
    """Get a driver class by name."""
    return DRIVER_REGISTRY.get(name)


def get_drivers_for_type(device_type: str) -> dict[str, type[BaseDriver]]:
    """Get all registered drivers compatible with a device type.

    Args:
        device_type: A DeviceType value (e.g. "grid_meter", "pv_inverter", "ev_charger").
    """
    from ionemo_drivers.base import (
        ACUnitDriver,
        DeviceType,
        EVChargerDriver,
        GridMeterDriver,
        PVInverterDriver,
    )

    type_map: dict[str, type[BaseDriver]] = {
        DeviceType.GRID_METER: GridMeterDriver,
        DeviceType.PV_INVERTER: PVInverterDriver,
        DeviceType.EV_CHARGER: EVChargerDriver,
        DeviceType.AC_UNIT: ACUnitDriver,
    }
    base_class = type_map.get(device_type)
    if base_class is None:
        return {}
    return {
        name: cls
        for name, cls in DRIVER_REGISTRY.items()
        if issubclass(cls, base_class)
    }


def load_all_drivers() -> None:
    """Load all builtin and external drivers. Call once at app startup."""
    _load_builtin_drivers()
    _load_external_drivers()
    logger.info("Driver registry: %d drivers loaded", len(DRIVER_REGISTRY))
