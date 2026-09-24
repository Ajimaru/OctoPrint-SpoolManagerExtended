# coding=utf-8

# Pure decision logic for "is this the tag's spool, or just a spool that shares its key?".
# Split out of the API layer so the branching is testable without flask or a database - same
# reasoning as RfidTeachIn.py next to it.
#
# A spool is matched through `rfidTagKey`: for a 4-byte UID the last few hex characters, for
# a 7/8-byte UID the whole UID (see U1RfidManager.deriveRfidTagKey()). The truncation is
# deliberate: some spools carry two physical tags whose 4-byte UIDs differ in their leading
# bytes but share the trailing ones, so a short key finds the spool from either side. The cost
# is a small key space, and collisions have been observed on real hardware - including a tag
# from another ecosystem resolving to an unrelated spool. That case was a TigerTag, i.e. a
# 7-byte NTAG UID, back when those were still keyed on their suffix too.
#
# This module never changes how a tag resolves; it only says whether the answer deserves a
# caveat. A warning, never a block: for a two-tag spool a shared-suffix match is the normal,
# correct case.
#
# Deliberately absent: a "matched on fewer characters than were scanned" flag. It was true for
# every tag measured - genuine collisions and ordinary spools alike - so it separated nothing
# and would have warned during normal operation. A flag that is always set carries no
# information. The foreign-tag case it aimed at is answered by the tag's format instead.


def isAmbiguous(matchingSpoolCount):
    """
    True when more than one spool carries the key that was just looked up.

    DatabaseManager.loadSpoolByRfidTagKey() resolves this silently by newest-first ordering, so
    without this the caller cannot tell a single unambiguous hit from an arbitrary pick between
    several equally valid ones.
    """
    # isinstance covers bool too (it subclasses int), and that is fine here: neither True nor
    # False exceeds 1, so a flag passed where a count belongs answers False either way. An
    # explicit bool guard would look like it prevents something and prevent nothing.
    if not isinstance(matchingSpoolCount, int):
        return False
    return matchingSpoolCount > 1


# Formats that are verified and well-formed but carry no SpoolManager id at all: a tag in one
# of these belongs to another tool's ecosystem and cannot legitimately be any spool in this
# database. Kept as ids rather than imported from TagFormats so this module stays free of
# plugin imports and testable on its own; the ids are part of the wire format and stable.
_FOREIGN_TAG_FORMATS = frozenset(("ntagTigerTag", "nfcvOpenPrintTag"))


def isForeignTagFormat(tagFormat):
    """
    True when a parser identified the tag as a format that carries no SpoolManager id.

    The read path parses the tag itself and therefore knows the format by name - a stronger
    answer than the firmware's idSource, and available on the Mifare path too. Anything
    unrecognized answers False: an unknown format is an open question, not a foreign tag.
    """
    return tagFormat in _FOREIGN_TAG_FORMATS


def isForeignTagPayload(idSource):
    """
    True when the tag on the reader carries a verified payload that is not ours.

    The OctoScale firmware reports "extendedNoId" for a tag whose format it recognized and
    verified but which holds no SpoolManager id - TigerTag and OpenPrintTag. Such a tag cannot
    legitimately belong to any spool in this database, so a spool found for it was found by
    its UID key alone and is a collision, not an identification. This is the signal that catches
    the reported case, where a foreign tag resolved to an unrelated spool.

    Every other value answers False, including the empty string an unfinished read or an older
    firmware sends - an unanswered question must not read as "checked, and it is foreign".
    """
    return idSource == "extendedNoId"


def describeMatch(matchingSpoolCount, idSource=None, tagFormat=None):
    """
    The caveat for one lookup result, as {"foreignTag": bool, "ambiguous": bool}.

    Callers supply whichever evidence they have: the read path knows the parsed format, the
    write path has only the firmware's idSource, and a lookup from a typed string has neither.
    Either source alone is enough to call a tag foreign - they answer the same question from
    two directions and never contradict each other for a tag that is genuinely ours.

    Both flags default to False, which is also what a caller on an older code path produces -
    so a missing answer never reads as "this match is fine".
    """
    return {
        "foreignTag": isForeignTagFormat(tagFormat) or isForeignTagPayload(idSource),
        "ambiguous": isAmbiguous(matchingSpoolCount),
    }
