from __future__ import annotations

from datetime import datetime
from functools import partial

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, Platform, UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER, DEVICE_MODEL
from .entity_registry import prune_stale_entities
from .webhook import SIGNAL_DATA_RECEIVED, SIGNAL_DEVICE_NEW

SENSOR_TYPES = {
    "battery_level": {
        "icon": None,
        "device_class": SensorDeviceClass.BATTERY,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": PERCENTAGE,
    },
    "wifi_signal": {
        "icon": "mdi:wifi",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": PERCENTAGE,
    },
    "sd_health": {
        "icon": "mdi:sd",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": PERCENTAGE,
    },
    "sd_capacity": {
        "icon": "mdi:sd",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfInformation.MEGABYTES,
    },
    "charging_status": {
        "icon": "mdi:battery-charging",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    "alarm_time": {
        "icon": "mdi:alarm-light",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "state_class": None,
        "unit": None,
    },
    "calling_time": {
        "icon": "mdi:phone",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "state_class": None,
        "unit": None,
    },
    "calling_action": {
        "icon": "mdi:phone-ring",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    "face_id": {
        "icon": "mdi:face-recognition",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    "alarm_type": {
        "icon": "mdi:alarm-panel",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    "last_event": {
        "icon": "mdi:history",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    "last_seen": {
        "icon": "mdi:clock-outline",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "state_class": None,
        "unit": None,
    },
}

# Per-alarm-type trigger time entities (alarm_time_<type>); their config is
# shared with the generic alarm_time sensor. Localized names live in
# translations/<lang>.json under entity.sensor.<key>. Unknown types fall
# back to the raw key.
ALARM_TIME_PREFIX = "alarm_time_"


def is_alarm_time_key(key: str) -> bool:
    return key.startswith(ALARM_TIME_PREFIX)


def sensor_config_for_key(key: str) -> dict | None:
    if key in SENSOR_TYPES:
        return SENSOR_TYPES[key]
    if is_alarm_time_key(key):
        return SENSOR_TYPES["alarm_time"]
    return None


def is_sensor_key(key: str) -> bool:
    return sensor_config_for_key(key) is not None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    device_manager = hass.data[DOMAIN]["device_manager"]
    created: dict[str, set[str]] = {}
    # Keys re-seeded from registry entries kept during cleanup below.
    seeded: dict[str, set[str]] = {}

    kept = prune_stale_entities(
        hass,
        config_entry,
        Platform.SENSOR,
        {
            f"{device_id}_{key}"
            for device_id in device_manager.get_all_devices()
            for key in device_manager.get_entity_keys(device_id)
            if is_sensor_key(key)
        },
    )
    # Entities from older versions that already hold values: keep them and
    # re-seed their keys so they persist as lazily-created entities.
    for unique_id in kept:
        for device_id in device_manager.get_all_devices():
            prefix = f"{device_id}_"
            if not unique_id.startswith(prefix):
                continue
            key = unique_id[len(prefix):]
            if not is_sensor_key(key):
                continue
            seeded.setdefault(device_id, set()).add(key)
            hass.async_create_task(device_manager.async_add_entity_key(device_id, key))
            break

    @callback
    def _add_missing(device_id: str, data: dict | None = None) -> None:
        # data=None → restore from persisted keys (startup) plus re-seeded
        # keys from this session's registry cleanup; otherwise create
        # entities for any new key carrying a real value.
        if data is None:
            restore_keys = (
                set(device_manager.get_entity_keys(device_id))
                | seeded.get(device_id, set())
            )
            candidates = [key for key in restore_keys if is_sensor_key(key)]
        else:
            candidates = [
                key for key, value in data.items()
                if is_sensor_key(key) and value is not None
            ]
        known = created.setdefault(device_id, set())
        new_entities = []
        for key in candidates:
            if key in known:
                continue
            known.add(key)
            entity = EZVIZSensor(device_id, key, sensor_config_for_key(key), device_manager)
            if data is not None:
                entity.apply_initial(data)
                hass.async_create_task(
                    device_manager.async_add_entity_key(device_id, key)
                )
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    @callback
    def _register_device(device_id: str) -> None:
        config_entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                f"{SIGNAL_DATA_RECEIVED}_{device_id}",
                partial(_add_missing, device_id),
            )
        )
        _add_missing(device_id)

    for device_id in device_manager.get_all_devices():
        _register_device(device_id)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, _register_device)
    )


class EZVIZSensor(RestoreEntity, SensorEntity):
    def __init__(self, device_id: str, sensor_key: str, sensor_config: dict, device_manager) -> None:
        self._device_id = device_id
        self._device_manager = device_manager
        self._sensor_key = sensor_key
        self._config = sensor_config
        self._attr_unique_id = f"{device_id}_{sensor_key}"
        self._attr_has_entity_name = True
        self._attr_translation_key = sensor_key
        self._attr_icon = sensor_config["icon"]
        self._attr_device_class = sensor_config["device_class"]
        self._attr_state_class = sensor_config["state_class"]
        self._attr_native_unit_of_measurement = sensor_config["unit"]
        self._attr_should_poll = False
        self._attr_available = False
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        # True when the entity was created carrying fresh data from its very
        # first message; restore must not overwrite it with older values.
        self._has_initial_value = False

    @property
    def device_info(self) -> DeviceInfo:
        device = self._device_manager.get_device(self._device_id)
        name = (
            device.friendly_name
            if device and device.friendly_name
            else f"EZVIZ {self._device_id[:8]}"
        )
        model = device.device_type if device and device.device_type else DEVICE_MODEL
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=name,
            manufacturer=MANUFACTURER,
            model=model,
        )

    async def async_added_to_hass(self) -> None:
        # Restore last known state after restart, unless the entity was just
        # created with fresh data from its first message.
        if not self._has_initial_value:
            if (last_state := await self.async_get_last_state()) is not None:
                if last_state.state not in ("unknown", "unavailable"):
                    value = last_state.state
                    if self._config["device_class"] == SensorDeviceClass.TIMESTAMP:
                        try:
                            dt = datetime.fromisoformat(value)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=dt_util.get_default_time_zone())
                            value = dt
                        except (ValueError, TypeError):
                            pass
                    elif self._config["unit"] is not None:
                        try:
                            value = float(value)
                        except (TypeError, ValueError):
                            pass
                    self._attr_native_value = value
                    self._attr_available = True

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_RECEIVED}_{self._device_id}",
                self._handle_data,
            )
        )

    @callback
    def _handle_data(self, data: dict) -> None:
        if self.apply_initial(data):
            self.async_write_ha_state()

    def apply_initial(self, data: dict) -> bool:
        """Apply values from extracted data without writing HA state.

        Used both by live updates and when the entity is created from the
        very first message carrying its value.
        """
        if self._sensor_key not in data:
            return False
        value = data[self._sensor_key]
        if value is None:
            return False
        if self._config["device_class"] == SensorDeviceClass.TIMESTAMP and isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_util.get_default_time_zone())
                value = dt
            except (ValueError, TypeError):
                pass
        if self._sensor_key == "face_id" and data.get("face_id_raw"):
            self._attr_extra_state_attributes["raw_face_id"] = data["face_id_raw"]
        self._attr_native_value = value
        self._attr_available = True
        self._has_initial_value = True
        return True
