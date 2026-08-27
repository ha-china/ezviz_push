"""Keep the entity registry aligned with lazily-created entities.

Entities whose data has never arrived are no longer added. Registry entries
left over from older versions are pruned only when they provably never held
a value; entries with real data are kept and their keys re-seeded so they
persist as lazily-created entities.

"Has data" must be checked against persistent sources, because at platform
setup time after a restart the current state machine is still empty:
- RestoreEntity kinds (sensor / binary_sensor): the restore-state store.
- Cameras: the image file persisted on disk.
"""

from __future__ import annotations

import logging
from typing import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreStateData

_LOGGER = logging.getLogger(__name__)


def _restored_state_str(hass: HomeAssistant, entity_id: str) -> str | None:
    """Last persisted state string across restarts, or None if unknown."""
    try:
        data = RestoreStateData.async_get_instance(hass)
    except Exception:  # pragma: no cover - defensive for HA internals change
        return None
    record = data.last_states.get(entity_id)
    if record is None or record.state is None:
        return None
    return record.state.state


def _default_keep(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    state = _restored_state_str(hass, entry.entity_id)
    return state is not None and state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)


def prune_stale_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    platform_domain: str,
    allowed_unique_ids: set[str],
    keep_check: Callable[[HomeAssistant, er.RegistryEntry], bool] = _default_keep,
) -> set[str]:
    """Remove never-populated registry entries of this integration+platform.

    Returns the set of unique_ids that were NOT removed because their entity
    had real data before (callers should seed these back as persisted keys).
    """
    registry = er.async_get(hass)
    kept: set[str] = set()
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.domain != platform_domain:
            continue
        if entry.unique_id in allowed_unique_ids:
            continue
        if keep_check(hass, entry):
            # Entity carried a real value from a previous version: keep it.
            kept.add(entry.unique_id)
            continue
        _LOGGER.debug(
            "Pruning stale %s entity %s (unique_id=%s)",
            platform_domain,
            entry.entity_id,
            entry.unique_id,
        )
        registry.async_remove(entry.entity_id)
    return kept


def split_unique_id(
    unique_id: str, known_keys: set[str]
) -> tuple[str | None, str | None]:
    """Split '<device_id>_<key>' using the known key list (keys may contain '_')."""
    for key in sorted(known_keys, key=len, reverse=True):
        suffix = f"_{key}"
        if unique_id.endswith(suffix):
            return unique_id[: -len(suffix)], key
    return None, None
