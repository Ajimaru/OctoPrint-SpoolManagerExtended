# coding=utf-8

# Pure decision logic for automatically teaching a spool its rfidTagKey after a successful
# OpenPrintTag write (issue: OpenPrintTag tags carry no SpoolManager database id, so reading
# one back falls to a UID lookup - see U1RfidManager.deriveRfidTagKey()'s docstring and
# DatabaseManager.loadSpoolByRfidTagKey()). Split out from SpoolManagerAPI.py's
# teachOctoScaleRfidTagKey() endpoint so the branching (no-op / blocked / save) is testable
# without flask or a database - see test_RfidTeachIn.py.
#
# Mirrors the existing Snapmaker U1 teach-in contract: never silently overwrite a key that
# already resolves to a different spool (or a different key already on the target spool)
# unless explicitly forced.

REASON_NO_UID = "noUid"
REASON_UNCHANGED = "unchanged"
REASON_EXISTING_KEY_DIFFERS = "existingKeyDiffers"
REASON_COLLISION = "collision"
REASON_TAUGHT = "taught"


def evaluateTeachIn(
    newKey, existingKeyOnTargetSpool, conflictingSpoolId, targetSpoolId, force
):
    """
    Decides whether a derived rfidTagKey should be saved onto the target spool.

    newKey: the rfidTagKey derived from the just-written tag's UID (may be None if the UID
        was absent/too short - see U1RfidManager.deriveRfidTagKey()).
    existingKeyOnTargetSpool: the target spool's current rfidTagKey value, if any.
    conflictingSpoolId: databaseId of whatever spool loadSpoolByRfidTagKey(newKey) currently
        resolves to, or None if nothing does.
    targetSpoolId: databaseId of the spool the tag was just written for.
    force: True to override an existing-key-differs or collision block.

    Returns (shouldSave, reason). shouldSave is True only when the caller should write
    newKey onto the target spool; reason is always one of the REASON_* constants above and
    explains the outcome either way (including the non-error "unchanged"/"taught" cases).
    """
    if not newKey:
        return False, REASON_NO_UID

    if existingKeyOnTargetSpool == newKey:
        return False, REASON_UNCHANGED

    if existingKeyOnTargetSpool and not force:
        return False, REASON_EXISTING_KEY_DIFFERS

    if (
        conflictingSpoolId is not None
        and conflictingSpoolId != targetSpoolId
        and not force
    ):
        return False, REASON_COLLISION

    return True, REASON_TAUGHT
