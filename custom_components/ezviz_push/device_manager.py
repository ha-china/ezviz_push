from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Dict, Optional

from homeassistant.helpers.storage import Store

STORAGE_KEY = "ezviz_push.devices"
STORAGE_VERSION = 1


@dataclass
class DeviceInfo:
    device_id: str
    first_seen: str
    last_seen: str
    message_count: int = 0
    friendly_name: str = ""
    device_type: str = ""
    entity_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "message_count": self.message_count,
            "friendly_name": self.friendly_name,
            "device_type": self.device_type,
            "entity_keys": self.entity_keys,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DeviceInfo:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class DeviceManager:
    SAVE_DELAY = 10

    def __init__(self, hass) -> None:
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._devices: Dict[str, DeviceInfo] = {}
        self._flush_handle = None

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            self._devices = {}
            return
        self._devices = {
            d["device_id"]: DeviceInfo.from_dict(d) for d in data
        }

    async def async_save(self) -> None:
        await self._store.async_save(
            [d.to_dict() for d in self._devices.values()]
        )

    def schedule_save(self) -> None:
        """Coalesce frequent mutations into one delayed write.

        Webhook handling must not block on disk I/O; EZVIZ requires the
        callback to respond within 2 s or it marks the push as failed.
        """
        if self._flush_handle is not None:
            return
        self._flush_handle = self._hass.loop.call_later(
            self.SAVE_DELAY,
            lambda: self._hass.async_create_task(self._delayed_flush()),
        )

    async def _delayed_flush(self) -> None:
        self._flush_handle = None
        await self.async_save()

    async def async_flush(self) -> None:
        """Cancel any pending debounce and write immediately."""
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
            await self.async_save()

    async def async_ensure_device(self, device_id: str) -> tuple[DeviceInfo, bool]:
        now = datetime.now().isoformat()
        existing = self._devices.get(device_id)
        if existing is not None:
            existing.last_seen = now
            existing.message_count += 1
            self.schedule_save()
            return existing, False

        info = DeviceInfo(
            device_id=device_id,
            first_seen=now,
            last_seen=now,
            message_count=1,
            friendly_name=f"EZVIZ {device_id[:8]}",
        )
        self._devices[device_id] = info
        self.schedule_save()
        return info, True

    async def async_update_device_name(self, device_id: str, name: str) -> None:
        device = self._devices.get(device_id)
        if device and name and device.friendly_name != name:
            device.friendly_name = name
            self.schedule_save()

    async def async_update_device_model(self, device_id: str, model: str) -> None:
        device = self._devices.get(device_id)
        if device and model and device.device_type != model:
            device.device_type = model
            self.schedule_save()

    async def async_add_entity_key(self, device_id: str, key: str) -> None:
        """Record that a data field has produced a real value for this device.

        Used to recreate lazily-created entities after restart.
        """
        device = self._devices.get(device_id)
        if device is None or key in device.entity_keys:
            return
        device.entity_keys.append(key)
        self.schedule_save()

    def get_entity_keys(self, device_id: str) -> list[str]:
        device = self._devices.get(device_id)
        return list(device.entity_keys) if device else []

    async def async_remove_device(self, device_id: str) -> bool:
        if self._devices.pop(device_id, None) is not None:
            await self.async_save()
            return True
        return False

    def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        return self._devices.get(device_id)

    def get_all_devices(self) -> Dict[str, DeviceInfo]:
        return self._devices.copy()

    def get_device_count(self) -> int:
        return len(self._devices)