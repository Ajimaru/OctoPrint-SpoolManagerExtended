# coding=utf-8

# Which payload gets written onto an NFC tag.
#
# Today the only writer is OctoScale's PN532, which speaks ISO 14443A (NTAG) and stores
# nothing but the spool's database id - every other attribute stays in the SpoolManager
# database and is looked up through that id.
#
# OpenPrintTag (https://github.com/OpenPrintTag/openprinttag-specification) puts the spool
# data itself on the tag, but needs ISO 15693 / NFC-V hardware (PN5180 or similar) which the
# current reader cannot drive. The format is registered here as unsupported so the UI can
# name it, the encoder in openprinttag.py can be exercised, and enabling it later is a flag
# flip plus a writer implementation rather than a redesign.

TAG_FORMAT_SPOOL_ID_NTAG = "spoolIdNtag"
TAG_FORMAT_OPENPRINTTAG = "openPrintTag"


def _buildSpoolIdPayload(spoolModel):
    return {"databaseId": spoolModel.databaseId}


TAG_FORMATS = {
    TAG_FORMAT_SPOOL_ID_NTAG: {
        "id": TAG_FORMAT_SPOOL_ID_NTAG,
        "label": "Spool ID (NTAG)",
        "supported": True,
        "buildPayload": _buildSpoolIdPayload,
        "description": "Writes only the database id; all spool data stays in SpoolManager.",
    },
    TAG_FORMAT_OPENPRINTTAG: {
        "id": TAG_FORMAT_OPENPRINTTAG,
        "label": "OpenPrintTag",
        # needs an ISO 15693 / NFC-V reader, the current OctoScale hardware (PN532) cannot write these
        "supported": False,
        "buildPayload": None,
        "description": "Stores the spool data on the tag itself. Requires NFC-V capable hardware.",
    },
}


def getTagFormat(formatId):
    return TAG_FORMATS.get(formatId)


def isSupported(formatId):
    tagFormat = getTagFormat(formatId)
    return tagFormat is not None and tagFormat["supported"]
