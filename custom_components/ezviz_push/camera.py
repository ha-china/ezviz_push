from __future__ import annotations

import logging
import os
from typing import Optional

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, DEVICE_MODEL
from .image_handler import ImageHandler
from .webhook import SIGNAL_DATA_RECEIVED, SIGNAL_DEVICE_NEW

_LOGGER = logging.getLogger(__name__)


def _image_dir(hass: HomeAssistant) -> str:
    path = hass.config.path("ezviz_push_images")
    os.makedirs(path, exist_ok=True)
    return path


def _image_path(hass: HomeAssistant, device_id: str, camera_key: str) -> str:
    return os.path.join(_image_dir(hass), f"{device_id}_{camera_key}.jpg")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    device_manager = hass.data[DOMAIN]["device_manager"]
    image_handler = hass.data[DOMAIN]["image_handler"]

    @callback
    def _device_added(device_id: str) -> None:
        entities = [
            EZVIZCamera(hass, device_id, "alarm_picture", "alarm_picture_url", image_handler, device_manager),
            EZVIZCamera(hass, device_id, "calling_picture", "calling_picture_url", image_handler, device_manager),
        ]
        async_add_entities(entities)

    for device_id in device_manager.get_all_devices():
        _device_added(device_id)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, _device_added)
    )


class EZVIZCamera(Camera):
    def __init__(
        self,
        hass: HomeAssistant,
        device_id: str,
        camera_key: str,
        url_key: str,
        image_handler: ImageHandler,
        device_manager,
    ) -> None:
        super().__init__()
        self._hass = hass
        self._device_id = device_id
        self._device_manager = device_manager
        self._camera_key = camera_key
        self._url_key = url_key
        self._image_handler = image_handler
        self._image_path = _image_path(hass, device_id, camera_key)
        self._attr_unique_id = f"{device_id}_{camera_key}"
        self._attr_has_entity_name = True
        self._attr_translation_key = camera_key
        self._attr_should_poll = False
        self._attr_available = False
        self._current_image: Optional[bytes] = None

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
        await self._image_handler.ensure_session()

        # Load last saved image from disk
        if os.path.exists(self._image_path):
            def _load():
                with open(self._image_path, "rb") as f:
                    return f.read()
            try:
                self._current_image = await self._hass.async_add_executor_job(_load)
                self._attr_available = True
                _LOGGER.info("Loaded saved image for %s/%s", self._device_id, self._camera_key)
            except Exception as err:
                _LOGGER.warning("Failed to load saved image: %s", err)

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_RECEIVED}_{self._device_id}",
                self._handle_data,
            )
        )

    @callback
    def _handle_data(self, data: dict) -> None:
        url = data.get(self._url_key)
        if url:
            self._attr_available = True
            self.hass.async_create_task(self._fetch_image(url))

    async def _fetch_image(self, url: str) -> None:
        encoded = await self._image_handler.fetch_image(url)
        if encoded:
            import base64
            self._current_image = base64.b64decode(encoded)

            # Persist to disk
            def _save():
                with open(self._image_path, "wb") as f:
                    f.write(self._current_image)
            try:
                await self._hass.async_add_executor_job(_save)
            except Exception as err:
                _LOGGER.warning("Failed to save image: %s", err)

            self.async_write_ha_state()

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        return self._current_image