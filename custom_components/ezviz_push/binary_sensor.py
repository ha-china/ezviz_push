from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MANUFACTURER, DEVICE_MODEL, MSG_TYPE_ALARM, MSG_TYPE_CALLING
from .webhook import SIGNAL_DATA_RECEIVED, SIGNAL_DEVICE_NEW


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    device_manager = hass.data[DOMAIN]["device_manager"]

    @callback
    def _device_added(device_id: str) -> None:
        entities = [
            EZVIZOnlineBinarySensor(device_id),
            EZVIZDoorbellRingBinarySensor(device_id),
            EZVIZAlarmBinarySensor(device_id),
            EZVIZArmedBinarySensor(device_id),
            EZVIZDetectionBinarySensor(device_id),
        ]
        async_add_entities(entities)

    for device_id in device_manager.get_all_devices():
        _device_added(device_id)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, _device_added)
    )


class _EZVIZBinarySensor(RestoreEntity, BinarySensorEntity):
    _attr_should_poll = False

    def _init(self, device_id: str, key: str, name: str, device_class=None) -> None:
        self._device_id = device_id
        self._key = key
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_available = False
        self._attr_is_on = False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=f"EZVIZ {self._device_id[:8]}",
            manufacturer=MANUFACTURER,
            model=DEVICE_MODEL,
        )

    async def async_added_to_hass(self) -> None:
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in ("unknown", "unavailable"):
                self._attr_is_on = last_state.state == "on"
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
        pass


class EZVIZOnlineBinarySensor(_EZVIZBinarySensor):
    def __init__(self, device_id: str) -> None:
        self._init(device_id, "online", "Online", BinarySensorDeviceClass.CONNECTIVITY)
        self._attr_available = True
        self._attr_is_on = True

    @callback
    def _handle_data(self, data: dict) -> None:
        self._attr_is_on = True
        self._attr_available = True
        self.async_write_ha_state()


class EZVIZDoorbellRingBinarySensor(_EZVIZBinarySensor):
    def __init__(self, device_id: str) -> None:
        self._init(device_id, "doorbell_ring", "Doorbell Ring", BinarySensorDeviceClass.OCCUPANCY)
        self._attr_available = True

    @callback
    def _handle_data(self, data: dict) -> None:
        if data.get("_message_type") == MSG_TYPE_CALLING:
            self._attr_is_on = True
            self._attr_available = True
            self.async_write_ha_state()
            self._timer = self.hass.loop.call_later(30, self._safe_reset)

    def _safe_reset(self) -> None:
        if self._attr_available:
            self._attr_is_on = False
            self.async_write_ha_state()


class EZVIZAlarmBinarySensor(_EZVIZBinarySensor):
    def __init__(self, device_id: str) -> None:
        self._init(device_id, "alarm", "Alarm", BinarySensorDeviceClass.SAFETY)
        self._attr_available = True

    @callback
    def _handle_data(self, data: dict) -> None:
        if data.get("_message_type") == MSG_TYPE_ALARM:
            self._attr_is_on = True
            self._attr_available = True
            self.async_write_ha_state()
            self._timer = self.hass.loop.call_later(30, self._safe_reset)

    def _safe_reset(self) -> None:
        if self._attr_available:
            self._attr_is_on = False
            self.async_write_ha_state()


class EZVIZArmedBinarySensor(_EZVIZBinarySensor):
    """布防状态 (status: 1=armed, 0=disarmed)"""

    def __init__(self, device_id: str) -> None:
        self._init(device_id, "armed", "Armed", BinarySensorDeviceClass.LOCK)

    @callback
    def _handle_data(self, data: dict) -> None:
        if "armed" in data and data["armed"] is not None:
            self._attr_is_on = bool(data["armed"])
            self._attr_available = True
            self.async_write_ha_state()


class EZVIZDetectionBinarySensor(_EZVIZBinarySensor):
    """检测开关 (Enable: 1=on, 0=off)"""

    def __init__(self, device_id: str) -> None:
        self._init(device_id, "detection_enabled", "Detection Enabled", BinarySensorDeviceClass.RUNNING)

    @callback
    def _handle_data(self, data: dict) -> None:
        if "detection_enabled" in data and data["detection_enabled"] is not None:
            self._attr_is_on = bool(data["detection_enabled"])
            self._attr_available = True
            self.async_write_ha_state()