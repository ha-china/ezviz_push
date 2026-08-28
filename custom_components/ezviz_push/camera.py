from __future__ import annotations

import logging
import os
from functools import partial
from typing import Optional

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, DEVICE_MODEL
from .entity_registry import prune_stale_entities
from .image_handler import ImageHandler
from .webhook import SIGNAL_DATA_RECEIVED, SIGNAL_DEVICE_NEW

_LOGGER = logging.getLogger(__name__)

# Static camera: calling cover picture. Alarm pictures are dynamic, one
# entity per alarm type; localized names live in translations/<lang>.json
# under entity.camera.<key>. Unknown types fall back to the raw key.
CALLING_CAMERA_KEY = "calling_picture"
ALARM_PICTURE_PREFIX = "alarm_picture_"


def url_key_for_camera_key(camera_key: str) -> Optional[str]:
    """Map an entity key to the extracted-data key carrying its picture URL."""
    if camera_key == CALLING_CAMERA_KEY:
        return "calling_picture_url"
    if camera_key.startswith(ALARM_PICTURE_PREFIX):
        return f"alarm_picture_url_{camera_key[len(ALARM_PICTURE_PREFIX):]}"
    return None


def camera_key_for_url_key(url_key: str) -> Optional[str]:
    """Map an extracted-data URL key to its entity key (or None)."""
    if url_key == "calling_picture_url":
        return CALLING_CAMERA_KEY
    if url_key.startswith("alarm_picture_url_"):
        return ALARM_PICTURE_PREFIX + url_key[len("alarm_picture_url_"):]
    return None


def is_camera_key(key: str) -> bool:
    return key == CALLING_CAMERA_KEY or key.startswith(ALARM_PICTURE_PREFIX)


def _image_dir(hass: HomeAssistant) -> str:
    path = hass.config.path("ezviz_push_images")
    os.makedirs(path, exist_ok=True)
    return path


def _image_path(hass: HomeAssistant, device_id: str, camera_key: str) -> str:
    return os.path.join(_image_dir(hass), f"{device_id}_{camera_key}.jpg")


def _keep_if_image_on_disk(hass: HomeAssistant, entry) -> bool:
    """Camera entities persist their last picture to disk; that is the
    reliable 'had data' signal across restarts."""
    return os.path.exists(
        hass.config.path("ezviz_push_images", f"{entry.unique_id}.jpg")
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    device_manager = hass.data[DOMAIN]["device_manager"]
    image_handler = hass.data[DOMAIN]["image_handler"]
    created: dict[str, set[str]] = {}
    # Keys re-seeded from registry entries kept during cleanup below.
    seeded: dict[str, set[str]] = {}

    kept = prune_stale_entities(
        hass,
        config_entry,
        Platform.CAMERA,
        {
            f"{device_id}_{key}"
            for device_id in device_manager.get_all_devices()
            for key in device_manager.get_entity_keys(device_id)
            if is_camera_key(key)
        },
        keep_check=_keep_if_image_on_disk,
    )
    for unique_id in kept:
        for device_id in device_manager.get_all_devices():
            prefix = f"{device_id}_"
            if not unique_id.startswith(prefix):
                continue
            key = unique_id[len(prefix):]
            if not is_camera_key(key):
                continue
            seeded.setdefault(device_id, set()).add(key)
            hass.async_create_task(device_manager.async_add_entity_key(device_id, key))
            break

    # Drop the legacy generic alarm_picture entity: pictures are now stored
    # per alarm type and must not overwrite each other.
    legacy_registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(
        legacy_registry, config_entry.entry_id
    ):
        if entry.domain != Platform.CAMERA:
            continue
        if not entry.unique_id.endswith("_alarm_picture"):
            continue
        _LOGGER.debug("Removing legacy generic alarm picture entity %s", entry.entity_id)
        legacy_registry.async_remove(entry.entity_id)
        legacy_path = hass.config.path("ezviz_push_images", f"{entry.unique_id}.jpg")
        if os.path.exists(legacy_path):
            try:
                os.remove(legacy_path)
            except OSError as err:
                _LOGGER.warning("Failed to remove legacy image %s: %s", legacy_path, err)

    @callback
    def _add_missing(device_id: str, data: dict | None = None) -> None:
        known = created.setdefault(device_id, set())
        new_entities = []
        if data is None:
            restore_keys = (
                set(device_manager.get_entity_keys(device_id))
                | seeded.get(device_id, set())
            )
            candidates = {
                key: url_key
                for key in restore_keys
                if (url_key := url_key_for_camera_key(key)) is not None
            }
        else:
            candidates = {}
            for url_key, value in data.items():
                key = camera_key_for_url_key(url_key)
                if key is not None and value:
                    candidates[key] = url_key
        for key, url_key in candidates.items():
            if key in known:
                continue
            known.add(key)
            entity = EZVIZCamera(
                hass, device_id, key, url_key, image_handler, device_manager
            )
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
        # Resolved at runtime from translations/<lang>.json; unknown alarm
        # types fall back to the key itself.
        self._attr_translation_key = camera_key
        self._attr_should_poll = False
        self._attr_available = False
        self._current_image: Optional[bytes] = None
        self._pending_url: Optional[str] = None

    def apply_initial(self, data: dict) -> bool:
        """Store the URL from the first message so it is fetched once added."""
        url = data.get(self._url_key)
        if not url:
            return False
        self._pending_url = url
        self._attr_available = True
        return True

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

        if self._pending_url:
            url, self._pending_url = self._pending_url, None
            self.hass.async_create_task(self._fetch_image(url))

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
