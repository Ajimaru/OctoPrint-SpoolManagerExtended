# coding=utf-8

# Key handling for vendor tags that protect their sectors with something other than the
# factory key.
#
# Ported from OpenRFID (https://github.com/suchmememanyskill/OpenRFID), GPL-3.0:
#   load_hex_key_from_config / load_text_key_from_config  <- src/tag/*/processor.py
#   the HKDF-SHA256 derivation                            <- src/tag/bambu/processor.py
#     (itself following https://github.com/Bambu-Research-Group/RFID-Tag-Guide)
# Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md and
# 3rdPartySoftware/OpenRFID/LICENSE.
#
# ---------------------------------------------------------------------------------------
# NO SECRET KEY MATERIAL IS SHIPPED WITH THIS PLUGIN, AND NONE EVER WILL BE.
#
# For the vendors that protect their tags with a confidential value (currently Bambu), what
# lives here are SHA256 *checksums* of the expected keys, which is what OpenRFID carries
# too. A checksum cannot be reversed into a key: it only answers "is what the user typed
# the right value?", it does not supply it. Without a key entered by the user the
# corresponding parser stays disabled and simply never claims a tag.
#
# Snapmaker is deliberately different and the distinction matters: its salts ARE in this
# file, because they are published as plain literals in a public repository and are not
# secret. What makes a Snapmaker tag's keys unique is the tag's own UID, not a hidden
# value, so there is nothing to withhold and nothing for the user to obtain.
#
# These keys come from third-party reverse engineering. Reading tags on spools you own is
# the intended use. Writing proprietary formats is not supported here and must not be
# added: vendor tags carry signed blocks, and a failed write destroys the tag.
# ---------------------------------------------------------------------------------------
#
# Divergences from upstream:
#  - upstream reads keys from a config file section per parser; here they come from one
#    settings dict, because this plugin has no per-parser configuration.
#  - upstream only logs when a key is missing or wrong. This module additionally reports a
#    *status* per key (missing / invalid / ok) so the settings UI can show it: a typo
#    otherwise looks exactly like no entry at all, since both end in a silent parser.

from __future__ import annotations

import hashlib
import hmac
import logging

_logger = logging.getLogger("octoprint.plugins.SpoolManager.common.FilamentTagKeys")

# SHA256 of the correct key bytes. Not secret, and useless for obtaining a key - it only
# lets a user check their own entry against a known-good value.
#
# DELIBERATELY EMPTY. The reference checksum would have to be copied from OpenRFID's parser
# sources, and no copy of that repository is vendored here (only its LICENSE is), so it
# cannot be verified at the time of writing. A checksum guessed or recalled from memory is
# worse than none: it would reject the *correct* key the user obtained and present it as a
# typo, which is undiagnosable from the UI.
#
# Until a value is filled in from the upstream source, the entry is accepted on format alone
# (see keyStatus) - a wrong key then simply fails to authenticate against the tag, which is
# a safe outcome that cannot corrupt anything.
BAMBU_SALT_HASH = None

STATUS_MISSING = "missing"
STATUS_INVALID = "invalid"
STATUS_OK = "ok"

# Settings keys under which the user's entries are stored. The names say what the value is
# for; they are not an instruction for how to obtain it, and no source is linked anywhere
# in this plugin.
KEY_BAMBU_SALT = "bambuSalt"

_EXPECTED_HASHES = {
    KEY_BAMBU_SALT: BAMBU_SALT_HASH,
}

# Which entries are hex-encoded bytes and which are plain text. Getting this wrong would
# hash the wrong bytes and reject a correct key.
_HEX_KEYS = (KEY_BAMBU_SALT,)


def _keyBytes(name, value):
    """The raw bytes for a user-entered key, or None if it cannot be decoded at all."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if name in _HEX_KEYS:
        cleaned = text.replace(" ", "").replace(":", "")
        try:
            return bytes.fromhex(cleaned)
        except ValueError:
            return None
    return text.encode("utf-8")


def keyStatus(name, value):
    """(status, keyBytes) for one entry. keyBytes is None unless the status is ok."""
    if name not in _EXPECTED_HASHES:
        return STATUS_MISSING, None

    keyData = _keyBytes(name, value)
    if keyData is None:
        # Covers both "nothing entered" and "entered, but not decodable as hex" - to the
        # user those are the same situation: no usable key.
        return STATUS_MISSING, None

    expected = _EXPECTED_HASHES.get(name)
    if expected is None:
        # No reference checksum available for this vendor (see the note at the top). The
        # entry is taken at face value; a wrong key then simply fails to authenticate
        # against the tag, which is a safe outcome - it cannot corrupt anything.
        return STATUS_OK, keyData

    digest = hashlib.sha256(keyData).hexdigest()
    # compare_digest rather than == so a wrong key cannot be narrowed down by timing. The
    # value is not a credential of the user's, but constant-time comparison costs nothing.
    if not hmac.compare_digest(digest.lower(), expected.lower()):
        return STATUS_INVALID, None
    return STATUS_OK, keyData


class FilamentTagKeyStore(object):
    """Validated vendor keys, built from the plugin settings.

    A key that is absent or does not match its checksum yields None, which disables the
    parser that needs it - exactly as upstream does. Parsers therefore switch themselves
    off instead of the registry filtering them, which keeps the dispatch free of special
    cases.
    """

    def __init__(self, settingsValue=None):
        self._keys = {}
        self._statuses = {}
        settingsValue = settingsValue or {}
        for name in _EXPECTED_HASHES:
            status, keyData = keyStatus(name, settingsValue.get(name))
            self._statuses[name] = status
            if status == STATUS_OK:
                self._keys[name] = keyData
            elif status == STATUS_INVALID:
                # Worth a log line: to the user this is indistinguishable from no entry,
                # because both end in the parser staying quiet.
                _logger.warning(
                    "Vendor tag key '%s' does not match its expected checksum - the parser "
                    "that needs it stays disabled",
                    name,
                )

    def get(self, name):
        """Validated key bytes, or None when unset/invalid."""
        return self._keys.get(name)

    def has(self, name):
        return name in self._keys

    def statuses(self):
        """{keyName: status} for the settings UI. Never contains key material."""
        return dict(self._statuses)


# Snapmaker's per-tag key derivation.
#
# Ported from paxx12-snapmaker-u1/spool-link-apps (GPL-3.0), file
# android-app/app/src/main/java/dev/pages/paxx12/spoollink/formats/SnapmakerFormat.kt
# (repository state 2026-07-30). Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md
# and 3rdPartySoftware/spool-link-apps/LICENSE.
#
# Unlike the other vendors here these salts are NOT secret - they are literals published in
# that repository, so nothing has to be obtained or entered by the user. There is no key to
# withhold: what makes a Snapmaker tag's keys unique is the tag's own UID, not a secret.
#
# This is HMAC-based but NOT HKDF: the expand step hashes info||0x01 directly under the
# extracted PRK, without HKDF's running-T chaining. Reusing deriveBambuKeys() here would
# produce plausible-looking keys that never authenticate.
SNAPMAKER_SALT_KEY_A = b"Snapmaker_qwertyuiop[,.;]"
SNAPMAKER_SALT_KEY_B = b"Snapmaker_qwertyuiop[,.;]_1q2w3e"


def deriveSnapmakerKeys(uid, keyType="a"):
    """The 16 per-sector Crypto1 keys for a Snapmaker tag, derived from its UID.

    uid must be the RAW UID BYTES, not a hex string - hashing the text form yields keys
    that look fine and authenticate against nothing.

    Returns a list of 16 lowercase 12-character hex strings, the wire form the reader
    expects, or None when no UID is available.
    """
    if not uid:
        return None
    if isinstance(uid, str):
        # Guard rather than convenience: the hex form is what is in circulation elsewhere
        # in this plugin, and silently hashing it would be the failure described above.
        try:
            uid = bytes.fromhex(uid)
        except ValueError:
            return None

    salt = SNAPMAKER_SALT_KEY_A if keyType == "a" else SNAPMAKER_SALT_KEY_B
    prk = hmac.new(salt, uid, hashlib.sha256).digest()

    keys = []
    for sector in range(16):
        info = ("key_%s_%d" % (keyType, sector)).encode("utf-8")
        # The trailing 0x01 is HKDF's counter block, and Crypto1 takes the first 6 of the
        # 32 bytes produced.
        keys.append(
            hmac.new(prk, info + bytes([1]), hashlib.sha256).digest()[:6].hex()
        )
    return keys


def deriveBambuKeys(uid, salt):
    """The 16 per-sector key A values for a Bambu tag, via HKDF-SHA256 over its UID.

    Returns None when no salt is configured, which is the normal state for anyone who has
    not entered one - the caller then skips the parser.
    """
    if not uid or not salt:
        return None

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError:  # pragma: no cover - cryptography is a hard dependency
        _logger.warning("cryptography is unavailable, cannot derive Bambu tag keys")
        return None

    # 16 sectors x 6 bytes, derived in one pass and then split.
    derived = HKDF(
        algorithm=hashes.SHA256(), length=16 * 6, salt=salt, info=b"RFID-A\0"
    ).derive(uid)
    return [derived[index * 6 : (index + 1) * 6].hex() for index in range(16)]
