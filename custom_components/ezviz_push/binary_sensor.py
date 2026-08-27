from __future__ import annotations

from functools import partial

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MANUFACTURER, DEVICE_MODEL, MSG_TYPE_ALARM, MSG_TYPE_CALLING
from .entity_registry import prune_stale_entities, split_unique_id
from .webhook import SIGNAL_DATA_RECEIVED, SIGNAL_DEVICE_NEW


def _evt_calling(data: dict) -> bool:
    return data.get("_message_type") == MSG_TYPE_CALLING


def _evt_alarm(data: dict) -> bool:
    return data.get("_message_type") == MSG_TYPE_ALARM


def _has_armed(data: dict) -> bool:
    return data.get("armed") is not None


def _has_detection(data: dict) -> bool:
    return data.get("detection_enabled") is not None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    device_manager = hass.data[DOMAIN]["device_manager"]
    created: dict[str, set[str]] = {}
    # Keys re-seeded from registry entries kept during cleanup below.
    seeded: dict[str, set[str]] = {}

    lazy_keys = set(LAZY_BINARY_SENSORS)
    kept = prune_stale_entities(
        hass,
        config_entry,
        Platform.BINARY_SENSOR,
        {
            f"{device_id}_{key}"
            for device_id in device_manager.get_all_devices()
            for key in {"online"} | lazy_keys
            if key == "online" or key in device_manager.get_entity_keys(device_id)
        },
    )
    for unique_id in kept:
        device_id, key = split_unique_id(unique_id, {"online"} | lazy_keys)
        if device_id is None or key == "online":
            continue
        seeded.setdefault(device_id, set()).add(key)
        hass.async_create_task(device_manager.async_add_entity_key(device_id, key))

    @callback
    def _add_missing(device_id: str, data: dict | None = None) -> None:
        known = created.setdefault(device_id, set())
        new_entities = []
        if data is None:
            # Restore from persisted keys plus re-seeded keys from this
            # session's registry cleanup (their persistence may still be
            # in-flight).
            restore_keys = (
                set(device_manager.get_entity_keys(device_id))
                | seeded.get(device_id, set())
            )
            candidates = [
                (key, None)
                for key in restore_keys
                if key in LAZY_BINARY_SENSORS
            ]
        else:
            candidates = [
                (key, check) for key, (check, _factory) in LAZY_BINARY_SENSORS.items()
                if check(data)
            ]
        for key, check in candidates:
            if key in known:
                continue
            known.add(key)
            factory = LAZY_BINARY_SENSORS[key][1]
            entity = factory(device_id, device_manager)
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
        # The connectivity sensor is the always-present anchor entity; the
        # rest are created lazily once their data first arrives.
        async_add_entities([EZVIZOnlineBinarySensor(device_id, device_manager)])
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


class _EZVIZBinarySensor(RestoreEntity, BinarySensorEntity):
    _attr_should_poll = False
    RESET_SECONDS = 30
    # Event-type sensors (ring/alarm) flip back to off automatically.
    _auto_reset = False

    def _init(
        self, device_id: str, key: str, device_manager, device_class=None
    ) -> None:
        self._device_id = device_id
        self._device_manager = device_manager
        self._key = key
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_has_entity_name = True
        self._attr_translation_key = key
        self._attr_device_class = device_class
        self._attr_available = False
        self._attr_is_on = False
        self._timer = None
        # Set True when created from live data so the auto-reset timer starts
        # once the entity is actually added to hass.
        self._pending_reset = False

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
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in ("unknown", "unavailable"):
                self._attr_is_on = last_state.state == "on"
                self._attr_available = True
        if self._pending_reset:
            self._start_reset_timer()
            self._pending_reset = False
        elif self._auto_reset and self._attr_is_on:
            # A restored "on" state from before a restart still needs its reset.
            self._start_reset_timer()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_RECEIVED}_{self._device_id}",
                self._handle_data,
            )
        )

    def _start_reset_timer(self) -> None:
        if self._timer is not None:
            return
        self._timer = self.hass.loop.call_later(
            self.RESET_SECONDS, self._safe_reset
        )

    @callback
    def _handle_data(self, data: dict) -> None:
        pass

    def apply_initial(self, data: dict) -> bool:
        """Apply state from extracted data before the entity is added."""
        return False

    async def async_will_remove_from_hass(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


class EZVIZOnlineBinarySensor(_EZVIZBinarySensor):
    def __init__(self, device_id: str, device_manager) -> None:
        self._init(device_id, "online", device_manager, BinarySensorDeviceClass.CONNECTIVITY)
        self._attr_available = True
        self._attr_is_on = True

    @callback
    def _handle_data(self, data: dict) -> None:
        if "online" in data:
            self._attr_is_on = bool(data["online"])
            self._attr_available = True
            self.async_write_ha_state()


class EZVIZDoorbellRingBinarySensor(_EZVIZBinarySensor):
    _auto_reset = True

    def __init__(self, device_id: str, device_manager) -> None:
        self._init(device_id, "doorbell_ring", device_manager, BinarySensorDeviceClass.OCCUPANCY)
        self._attr_available = True

    def apply_initial(self, data: dict) -> bool:
        if not _evt_calling(data):
            return False
        self._attr_is_on = True
        self._attr_available = True
        self._pending_reset = True
        return True

    @callback
    def _handle_data(self, data: dict) -> None:
        if self.apply_initial(data):
            self._pending_reset = False
            self._start_reset_timer()
            self.async_write_ha_state()

    def _safe_reset(self, *_args: object) -> None:
        if self._attr_available:
            self._attr_is_on = False
            self.async_write_ha_state()


class EZVIZAlarmBinarySensor(_EZVIZBinarySensor):
    _auto_reset = True

    def __init__(self, device_id: str, device_manager) -> None:
        self._init(device_id, "alarm", device_manager, BinarySensorDeviceClass.SAFETY)
        self._attr_available = True

    def apply_initial(self, data: dict) -> bool:
        if not _evt_alarm(data):
            return False
        self._attr_is_on = True
        self._attr_available = True
        self._pending_reset = True
        return True

    @callback
    def _handle_data(self, data: dict) -> None:
        if self.apply_initial(data):
            self._pending_reset = False
            self._start_reset_timer()
            self.async_write_ha_state()

    def _safe_reset(self, *_args: object) -> None:
        if self._attr_available:
            self._attr_is_on = False
            self.async_write_ha_state()


class EZVIZArmedBinarySensor(_EZVIZBinarySensor):
    """布防状态 (status: 1=armed, 0=disarmed)"""

    def __init__(self, device_id: str, device_manager) -> None:
        self._init(device_id, "armed", device_manager, BinarySensorDeviceClass.LOCK)

    def apply_initial(self, data: dict) -> bool:
        if not _has_armed(data):
            return False
        self._attr_is_on = bool(data["armed"])
        self._attr_available = True
        return True

    @callback
    def _handle_data(self, data: dict) -> None:
        if self.apply_initial(data):
            self.async_write_ha_state()


class EZVIZDetectionBinarySensor(_EZVIZBinarySensor):
    """检测开关 (Enable: 1=on, 0=off)"""

    def __init__(self, device_id: str, device_manager) -> None:
        self._init(device_id, "detection_enabled", device_manager, BinarySensorDeviceClass.RUNNING)

    def apply_initial(self, data: dict) -> bool:
        if not _has_detection(data):
            return False
        self._attr_is_on = bool(data["detection_enabled"])
        self._attr_available = True
        return True

    @callback
    def _handle_data(self, data: dict) -> None:
        if self.apply_initial(data):
            self.async_write_ha_state()


# key → (trigger predicate over extracted data, entity factory)
LAZY_BINARY_SENSORS: dict[str, tuple] = {
    "doorbell_ring": (_evt_calling, EZVIZDoorbellRingBinarySensor),
    "alarm": (_evt_alarm, EZVIZAlarmBinarySensor),
    "armed": (_has_armed, EZVIZArmedBinarySensor),
    "detection_enabled": (_has_detection, EZVIZDetectionBinarySensor),
}
