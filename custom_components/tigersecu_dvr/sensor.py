"""Sensor platform for the Tigersecu DVR integration."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from . import TigersecuDVR
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tigersecu DVR sensor platform."""
    dvr: TigersecuDVR = hass.data[DOMAIN][entry.entry_id]
    sensors: list[SensorEntity] = []

    # Network Sensors
    network_sensors = [
        NetworkSensor(dvr, "ip", "IP Address", "mdi:ip-network"),
        NetworkSensor(dvr, "mac", "MAC Address", "mdi:network"),
        NetworkSensor(dvr, "gateway", "Gateway", "mdi:router-network"),
        NetworkSensor(dvr, "external_ip", "External IP", "mdi:wan"),
        NetworkSensor(dvr, "speed", "Link Speed", "mdi:speedometer", "Mbps"),
    ]
    sensors.extend(network_sensors)

    # Last Login Sensor
    sensors.append(LastLoginSensor(dvr))

    # Disk Scheme Sensor
    sensors.append(TigersecuDiskSchemeSensor(dvr))

    # Disk and SMART Sensors
    for disk_id in dvr.coordinator.data.get("disks", {}):
        sensors.append(DiskSensor(dvr, disk_id, "model", "Model", "mdi:harddisk"))
        sensors.append(DiskSensor(dvr, disk_id, "status", "Status", "mdi:list-status"))
        sensors.append(
            DiskSensor(dvr, disk_id, "capacity_gb", "Capacity", "mdi:database", "GB")
        )
        sensors.append(
            DiskSensor(
                dvr, disk_id, "available_gb", "Available", "mdi:database-check", "GB"
            )
        )
        for attr_id in dvr.coordinator.data["disks"][disk_id].get(
            "smart_attributes", {}
        ):
            sensors.append(SmartAttributeSensor(dvr, disk_id, attr_id))

    async_add_entities(sensors)


class TigersecuSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Tigersecu DVR sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, dvr: TigersecuDVR):
        """Initialize the sensor."""
        super().__init__(dvr.coordinator)
        self._dvr = dvr
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._dvr.entry.entry_id)},
        }


class NetworkSensor(TigersecuSensorBase):
    """A sensor for a network attribute."""

    def __init__(
        self, dvr: TigersecuDVR, key: str, name: str, icon: str, unit: str | None = None
    ):
        """Initialize the network sensor."""
        super().__init__(dvr)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{self._dvr.entry.entry_id}_network_{self._key}"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self.coordinator.data.get("network", {}).get(self._key)


class LastLoginSensor(TigersecuSensorBase):
    """A sensor for the last login event."""

    _attr_icon = "mdi:account-arrow-right"

    def __init__(self, dvr: TigersecuDVR):
        """Initialize the last login sensor."""
        super().__init__(dvr)
        self._attr_name = "Last Login"
        self._attr_unique_id = f"{self._dvr.entry.entry_id}_last_login"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        login_info = self.coordinator.data.get("last_login")
        if not login_info:
            return None
        return f"{login_info.get('user')} from {login_info.get('from')}"


class TigersecuDiskSchemeSensor(TigersecuSensorBase):
    """A sensor for the disk recording scheme of a Tigersecu DVR."""

    _attr_icon = "mdi:record-rec"

    def __init__(self, dvr: "TigersecuDVR") -> None:
        """Initialize the disk scheme sensor."""
        super().__init__(dvr)
        self._attr_name = "Disk Scheme"
        self._attr_unique_id = f"{self._dvr.entry.entry_id}_disk_scheme"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        scheme_id = self.coordinator.data.get("disk_scheme")

        if scheme_id is None:
            return None

        if scheme_id < 0:
            return "Not Recording"
        if scheme_id == 0:
            return "Continuous Recording"

        return "Scheduled/Motion Recording"


class DiskSensor(TigersecuSensorBase):
    """A sensor for a disk attribute."""

    def __init__(
        self,
        dvr: TigersecuDVR,
        disk_id: int,
        key: str,
        name: str,
        icon: str,
        unit: str | None = None,
    ):
        """Initialize the disk sensor."""
        super().__init__(dvr)
        self._disk_id = disk_id
        self._key = key
        self._attr_name = f"Disk {self._disk_id + 1} {name}"
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = (
            f"{self._dvr.entry.entry_id}_disk_{self._disk_id}_{self._key}"
        )

    @property
    def native_value(self) -> str | int | float | None:
        """Return the state of the sensor."""
        return (
            self.coordinator.data.get("disks", {}).get(self._disk_id, {}).get(self._key)
        )


class SmartAttributeSensor(TigersecuSensorBase):
    """A sensor for a disk SMART attribute."""

    _attr_icon = "mdi:information-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(self, dvr: TigersecuDVR, disk_id: int, attr_id: int):
        """Initialize the SMART attribute sensor."""
        super().__init__(dvr)
        self._disk_id = disk_id
        self._attr_id = attr_id
        self._attr_name = f"Disk {self._disk_id + 1} SMART {self._attr_id}"
        self._attr_unique_id = (
            f"{self._dvr.entry.entry_id}_disk_{self._disk_id}_smart_{self._attr_id}"
        )

    @property
    def _attribute_data(self) -> dict | None:
        """Get the data for this specific attribute."""
        return (
            self.coordinator.data.get("disks", {})
            .get(self._disk_id, {})
            .get("smart_attributes", {})
            .get(self._attr_id)
        )

    @property
    def native_value(self) -> int | None:
        """Return the 'Value' of the SMART attribute."""
        if data := self._attribute_data:
            value_str = data.get("value")
            if value_str is not None:
                try:
                    return int(value_str)
                except (ValueError, TypeError):
                    return None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return the other SMART data as attributes."""
        if data := self._attribute_data:
            return {
                "worst": data.get("worst"),
                "threshold": data.get("threshold"),
                "raw": data.get("raw"),
            }
        return None
