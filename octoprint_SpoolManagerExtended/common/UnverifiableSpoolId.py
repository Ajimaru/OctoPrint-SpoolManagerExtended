# coding=utf-8

# Pure decision logic for "is the spool id the firmware reports on this tag actually
# trustworthy?". Split out of SpoolManagerAPI.py's /octoscale/nfc endpoint so the branching is
# testable without flask or a database - same reasoning as RfidTeachIn.py next to it.
#
# Background (measured on firmware v0.0.3 against real hardware, 2026-09-18): OctoScale has a
# legacy write path, pn5180WriteNtagId() behind /nfcwriteid, that stores the database id as bare
# ASCII decimal digits space-padded across NTAG pages 4-6 - no magic, no NDEF, no CRC. Every
# other format this plugin knows is self-describing (magic "OX" at page 4 for ntagExtended, "OS"
# for the Mifare Classic and NFC-V variants, 0x5BF59264 for TigerTag), so a tag written in one of
# those can be recognized as genuinely ours. A legacy id cannot: bare digits on a tag are
# indistinguishable from bare digits some other system wrote there.
#
# The firmware reports such a tag as occupancy "foreign". That verdict is *not* a format
# judgement - pn5180NtagOccupancy() only asks "is the CC NDEF-formatted while the data area holds
# something that is not an empty NDEF message", i.e. it means "carries data". A legitimate legacy
# tag lands in that branch just as much as a Bambu tag does, which is why "occupancy is foreign,
# therefore discard the id" would quietly break working legacy tags and is deliberately NOT what
# this module does.
#
# What it does instead: flag the one combination where the id is both unverifiable *and*
# unusable - an id parsed out of the non-self-describing area that resolves to no spool in this
# database. Those are the tags a user has no way to reconcile, and the dialog should say so
# honestly rather than presenting the number as established fact.


def isSpoolIdUnverifiable(spoolId, occupancy, hasExtendedData, spoolExistsInDatabase):
    """
    Decides whether the spool id read off a tag should be treated as unverifiable.

    spoolId: the id the firmware parsed off the tag, or None when it reported none.
    occupancy: the firmware's occupancy verdict ("empty" | "foreign" | "", older firmware omits
        it and the caller passes "").
    hasExtendedData: True when the tag carries a self-describing OctoScale payload - that payload
        is magic- and CRC-checked, so an id read from it is trustworthy regardless of occupancy.
    spoolExistsInDatabase: True when spoolId resolves to a spool here.

    Returns True only when all of: an id was reported, the firmware saw data it could not
    identify, the tag carries no verified extended payload, and the id matches no spool in this
    database. Any of those failing means the normal (unchanged) handling applies.
    """
    if spoolId is None:
        return False

    # A verified extended payload carries its own magic + CRC, so its id stands on its own.
    #
    # This check is NOT redundant with the occupancy check below, and the implication does not
    # run the way it looks: the firmware only computes occupancy inside its
    # "if (!extendedCacheHasExtended)" branch (main.cpp:3979 and :4085ff in firmware 7697f3a),
    # so a tag carrying extended data never reports "foreign" at all - the field stays at its
    # init value "". Measured: the ntagExtended reference tag reports occupancy "" with
    # hasExtendedData true. Dropping this line would therefore not just be harmless duplication,
    # it would leave the extended case resting on a field that is structurally silent for it.
    if hasExtendedData is True:
        return False

    # "foreign" is the only verdict that says the data area holds something unrecognized. "empty"
    # and "" (older firmware) are not grounds to doubt an id.
    if occupancy != "foreign":
        return False

    # The decisive half: an id that resolves to a real spool is reconcilable by the user even
    # when it came from the legacy area, so it keeps the existing overwrite flow. Only an id
    # pointing at nothing leaves them with no way forward.
    return spoolExistsInDatabase is not True
