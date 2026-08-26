# coding=utf-8

# Ported from OpenRFID (https://github.com/suchmememanyskill/OpenRFID), GPL-3.0,
# src/tag/ndef_tag_processor.py. OpenRFID's version is itself adapted from
# https://github.com/paxx12/SnapmakerU1-Extended-Firmware (filament_protocol_ndef.py).
# Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md and
# 3rdPartySoftware/OpenRFID/LICENSE.
#
# Divergences from upstream:
#  - parseNdefRecords() is a plain function instead of a base class method: the parser
#    classes here compose rather than inherit, which keeps this module dependency-free and
#    unit-testable on its own.
#  - the hex dump helper is dropped (debug logging only).
#  - the URI prefix table is included because SpoolEase encodes its payload in a URI
#    record; upstream carries the same table.

from __future__ import annotations

import io

NDEF_OK = 0
NDEF_ERR = -1
NDEF_PARAMETER_ERR = -2
NDEF_NOT_FOUND_ERR = -3

TNF_WELL_KNOWN = 0x01
TNF_MIME = 0x02

# Standard NDEF URI abbreviation table: the first payload byte of a URI record indexes it.
NDEF_URI_PREFIX_MAP = {
    0x00: "",
    0x01: "http://www.",
    0x02: "https://www.",
    0x03: "http://",
    0x04: "https://",
    0x05: "tel:",
    0x06: "mailto:",
    0x07: "ftp://anonymous:anonymous@",
    0x08: "ftp://ftp.",
    0x09: "ftps://",
    0x0A: "sftp://",
    0x0B: "smb://",
    0x0C: "nfs://",
    0x0D: "ftp://",
    0x0E: "dav://",
    0x0F: "news:",
    0x10: "telnet://",
    0x11: "imap:",
    0x12: "rtsp://",
    0x13: "urn:",
    0x14: "pop:",
    0x15: "sip:",
    0x16: "sips:",
    0x17: "tftp:",
    0x18: "btspp://",
    0x19: "btl2cap://",
    0x1A: "btgoep://",
    0x1B: "tcpobex://",
    0x1C: "irdaobex://",
    0x1D: "file://",
    0x1E: "urn:epc:id:",
    0x1F: "urn:epc:tag:",
    0x20: "urn:epc:pat:",
    0x21: "urn:epc:raw:",
    0x22: "urn:epc:",
    0x23: "urn:nfc:",
}


class NdefRecord(object):
    def __init__(self, payload, tnf, recordType="", mimeType=""):
        self.tnf = tnf
        self.type = recordType
        self.mime_type = mimeType
        self.payload = payload

    def uriText(self):
        """Decoded URI for a well-known URI record, else None."""
        if self.tnf != TNF_WELL_KNOWN or self.type != "U" or not self.payload:
            return None
        prefix = NDEF_URI_PREFIX_MAP.get(self.payload[0], "")
        return prefix + self.payload[1:].decode("utf-8", errors="ignore")


def parseNdefRecords(dataBuffer):
    """(errorCode, [NdefRecord]) for the raw page dump of an NTAG/Ultralight tag."""
    if dataBuffer is None or not isinstance(dataBuffer, (list, bytes, bytearray)):
        return NDEF_PARAMETER_ERR, []

    try:
        data = bytes(dataBuffer)
        dataIo = io.BytesIO(data)

        # The capability container starts with 0xE1. A dump that begins at page 0 carries
        # UID and lock bytes first, so scan the first few bytes for it rather than assuming
        # a fixed offset.
        startOffset = 0
        if len(data) > 12 and data[0] != 0xE1:
            for index in range(min(16, len(data) - 4)):
                if data[index] == 0xE1 and data[index + 1] in (0x10, 0x11, 0x40):
                    startOffset = index
                    break
        if startOffset > 0:
            dataIo.seek(startOffset)

        cc = dataIo.read(4)
        if len(cc) < 4 or cc[0] != 0xE1:
            return NDEF_PARAMETER_ERR, []

        records = []

        while True:
            baseTlv = dataIo.read(2)
            if len(baseTlv) < 2:
                break

            tag = baseTlv[0]
            if tag == 0xFE:  # terminator TLV
                break

            tlvLength = baseTlv[1]
            if tlvLength == 0xFF:  # three-byte length form
                extendedLength = dataIo.read(2)
                if len(extendedLength) < 2:
                    break
                tlvLength = (extendedLength[0] << 8) | extendedLength[1]

            if tag != 0x03:  # not an NDEF message TLV - skip its payload
                dataIo.seek(tlvLength, 1)
                continue

            ndefData = dataIo.read(tlvLength)
            offset = 0

            while offset < len(ndefData) - 2:
                header = ndefData[offset]
                offset += 1

                tnf = header & 0x07
                shortRecord = (header >> 4) & 0x01
                hasIdLength = (header >> 3) & 0x01

                typeLength = ndefData[offset]
                offset += 1

                if shortRecord:
                    payloadLength = ndefData[offset]
                    offset += 1
                else:
                    if offset + 4 > len(ndefData):
                        break
                    payloadLength = (
                        (ndefData[offset] << 24)
                        | (ndefData[offset + 1] << 16)
                        | (ndefData[offset + 2] << 8)
                        | ndefData[offset + 3]
                    )
                    offset += 4

                idLength = 0
                if hasIdLength:
                    idLength = ndefData[offset]
                    offset += 1

                if offset + typeLength + idLength + payloadLength > len(ndefData):
                    break

                recordType = ndefData[offset : offset + typeLength].decode(
                    "ascii", errors="ignore"
                )
                offset += typeLength

                if idLength > 0:
                    offset += idLength

                payload = bytes(ndefData[offset : offset + payloadLength])
                offset += payloadLength

                if tnf == TNF_MIME:
                    records.append(
                        NdefRecord(
                            payload, tnf, recordType=recordType, mimeType=recordType
                        )
                    )
                elif tnf == TNF_WELL_KNOWN and recordType == "U":
                    records.append(NdefRecord(payload, tnf, recordType=recordType))

        if not records:
            return NDEF_NOT_FOUND_ERR, []

        return NDEF_OK, records

    except Exception:
        # A malformed tag must never take down the read - the caller treats an error code
        # exactly like "this parser does not recognize the tag".
        return NDEF_ERR, []
