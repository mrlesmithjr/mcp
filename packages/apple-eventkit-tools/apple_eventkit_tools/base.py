"""Shared EventKit store initialization and access request logic."""

import logging
import threading

import EventKit

logger = logging.getLogger(__name__)


class EventKitBase:
    """Base class for EventKit managers.

    Subclasses must implement _access_config() to return a tuple of:
        (entity_type_int, full_access_method_name, error_label)
    """

    def __init__(self):
        self._store = None

    @property
    def store(self):
        """Lazy-init the event store with permission request."""
        if self._store is None:
            self._store = EventKit.EKEventStore.alloc().init()
            self._request_access()
        return self._store

    def _access_config(self):
        """Return (entity_type_int, full_access_method_name, error_label)."""
        raise NotImplementedError

    def _request_access(self):
        """Request EventKit access (blocks until user responds to TCC prompt)."""
        entity_type, full_access_method, error_label = self._access_config()

        granted_flag = threading.Event()
        access_result = {"granted": False, "error": None}

        def callback(granted, error):
            access_result["granted"] = granted
            access_result["error"] = error
            granted_flag.set()

        if hasattr(self._store, full_access_method):
            getattr(self._store, full_access_method)(callback)
        else:
            self._store.requestAccessToEntityType_completion_(entity_type, callback)

        granted_flag.wait(timeout=30)

        if not access_result["granted"]:
            err = access_result["error"]
            msg = str(err) if err else f"{error_label} access denied"
            raise RuntimeError(f"{msg}. Grant access in System Settings > Privacy & Security > {error_label}.")

        logger.info("%s access granted", error_label)
