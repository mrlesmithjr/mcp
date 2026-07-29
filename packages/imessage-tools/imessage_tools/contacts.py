"""Lightweight contact resolution - maps phone numbers/emails to display names.

Uses CNContactStore (PyObjC) directly when available, falling back to an
AppleScript-based bulk dump when PyObjC is not installed in the current
environment. Caches results in memory for the lifetime of the MCP server.
"""

import logging
import subprocess
import threading

logger = logging.getLogger(__name__)

_AVAILABLE = True
try:
    import Contacts
except ImportError:
    _AVAILABLE = False
    logger.info("PyObjC Contacts framework not available - will use AppleScript fallback")


class ContactResolver:
    """Resolve phone numbers and emails to contact names."""

    def __init__(self):
        self._store = None
        self._cache: dict[str, str] = {}  # normalized handle → display name
        self._loaded = False

    def _init_store(self):
        """Lazy-init CNContactStore with permission request."""
        if not _AVAILABLE or self._store is not None:
            return
        try:
            self._store = Contacts.CNContactStore.alloc().init()
            granted_flag = threading.Event()
            access_result = {"granted": False}

            def callback(granted, error):
                access_result["granted"] = granted
                granted_flag.set()

            self._store.requestAccessForEntityType_completionHandler_(0, callback)
            granted_flag.wait(timeout=10)

            if not access_result["granted"]:
                logger.warning("Contacts access not granted - resolution disabled")
                self._store = None
        except Exception as e:
            logger.warning("Failed to init CNContactStore: %s", e)
            self._store = None

    def _load_all(self):
        """Build the phone/email → name cache from all contacts."""
        if self._loaded:
            return
        if _AVAILABLE:
            self._load_via_pyobjc()
        else:
            self._load_via_applescript()

    def _load_via_pyobjc(self):
        """Load contacts using the CNContactStore PyObjC framework."""
        self._init_store()
        if not self._store:
            self._loaded = True
            return

        try:
            keys = [
                Contacts.CNContactGivenNameKey,
                Contacts.CNContactFamilyNameKey,
                Contacts.CNContactPhoneNumbersKey,
                Contacts.CNContactEmailAddressesKey,
            ]
            request = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(keys)
            results = []

            def handler(contact, stop):
                results.append(contact)

            self._store.enumerateContactsWithFetchRequest_error_usingBlock_(request, None, handler)

            for contact in results:
                given = contact.givenName() or ""
                family = contact.familyName() or ""
                name = f"{given} {family}".strip()
                if not name:
                    continue

                # Cache all phone numbers
                for phone in contact.phoneNumbers():
                    value = phone.value().stringValue()
                    normalized = self._normalize(value)
                    if normalized:
                        self._cache[normalized] = name

                # Cache all emails
                for email in contact.emailAddresses():
                    value = email.value()
                    if value:
                        self._cache[value.lower()] = name

            logger.info("Contact resolver loaded %d handle→name mappings (PyObjC)", len(self._cache))
        except Exception as e:
            logger.warning("Failed to load contacts via PyObjC: %s", e)
        finally:
            self._loaded = True

    def _load_via_applescript(self):
        """Load contacts using AppleScript as a fallback when PyObjC is unavailable.

        The script dumps all contacts as 'Name:::handle' lines (one per phone/email).
        Phone numbers come out formatted (e.g. '+1 (555) 555-0142'); _normalize()
        strips them to the same canonical form used for chat.db identifiers.
        """
        script = """\
tell application "Contacts"
    set output to ""
    repeat with aPerson in every person
        set personName to name of aPerson
        repeat with aPhone in phones of aPerson
            set phoneVal to value of aPhone
            set output to output & personName & ":::" & phoneVal & linefeed
        end repeat
        repeat with anEmail in emails of aPerson
            set emailVal to value of anEmail
            set output to output & personName & ":::" & emailVal & linefeed
        end repeat
    end repeat
    return output
end tell"""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning("AppleScript contacts dump failed: %s", result.stderr.strip())
                return

            for line in result.stdout.splitlines():
                line = line.strip()
                if ":::" not in line:
                    continue
                name, _, handle = line.partition(":::")
                name = name.strip()
                handle = handle.strip()
                if not name or not handle:
                    continue

                if "@" in handle:
                    self._cache[handle.lower()] = name
                else:
                    normalized = self._normalize(handle)
                    if normalized:
                        self._cache[normalized] = name

            logger.info(
                "Contact resolver loaded %d handle→name mappings (AppleScript)",
                len(self._cache),
            )
        except Exception as e:
            logger.warning("Failed to load contacts via AppleScript: %s", e)
        finally:
            self._loaded = True

    @staticmethod
    def _normalize(phone: str) -> str:
        """Normalize a phone number to digits-only with country code."""
        digits = "".join(c for c in phone if c.isdigit())
        # Add US country code if 10 digits
        if len(digits) == 10:
            digits = "1" + digits
        if digits:
            return "+" + digits
        return ""

    def resolve(self, handle: str) -> str | None:
        """Resolve a phone number or email to a contact name.

        Returns the display name or None if not found.
        """
        self._load_all()
        # Try direct lookup
        normalized = self._normalize(handle) if "@" not in handle else handle.lower()
        return self._cache.get(normalized)

    def resolve_or_handle(self, handle: str) -> str:
        """Resolve a handle to a name, falling back to the handle itself."""
        return self.resolve(handle) or handle

    def search_by_name(self, name: str) -> list[dict]:
        """Search contacts by name, return matching phone numbers.

        Used for send_message("Jane Doe", ...) → resolve to phone number.
        """
        self._load_all()
        if not _AVAILABLE or not self._store:
            return []

        try:
            keys = [
                Contacts.CNContactGivenNameKey,
                Contacts.CNContactFamilyNameKey,
                Contacts.CNContactPhoneNumbersKey,
            ]
            predicate = Contacts.CNContact.predicateForContactsMatchingName_(name)
            contacts, error = self._store.unifiedContactsMatchingPredicate_keysToFetch_error_(predicate, keys, None)
            if error:
                return []

            results = []
            for contact in contacts:
                given = contact.givenName() or ""
                family = contact.familyName() or ""
                display = f"{given} {family}".strip()
                phones = []
                for phone in contact.phoneNumbers():
                    label = phone.label() or ""
                    # Clean up Apple's label format
                    label = label.replace("_$!<", "").replace(">!$_", "")
                    value = phone.value().stringValue()
                    phones.append({"label": label, "number": self._normalize(value) or value})
                if phones:
                    results.append({"name": display, "phones": phones})

            return results
        except Exception as e:
            logger.warning("Contact search failed: %s", e)
            return []
