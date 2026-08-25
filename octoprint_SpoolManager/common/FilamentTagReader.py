# coding=utf-8

# Reads raw tag bytes from an OctoScale device and hands them to the parsers.
#
# This is the seam that lets the ported OpenRFID parsers work unchanged: upstream they sit
# on top of an SPI/GPIO reader, here on top of OctoScale's HTTP API. Everything above this
# module (parsers, mapping, UI) is unaware of which one it is.
#
# Deliberately free of flask/requests/OctoPrint imports so it stays unit-testable with the
# path-based harness in test/: the caller passes in a `callOctoScale` callable rather than
# the plugin. See SpoolManagerAPI._callOctoScale for the real one.

from __future__ import annotations

import time

from .FilamentTagModel import ScanResult, tagTypeFromOctoScale

# The read runs as a job on the device: /nfcreadstart accepts it (202) and the result is
# polled from /nfcreadstatus. Measured worst case on real hardware is ~2.2 s for a full 1K
# Mifare Classic dump, so this budget is generous rather than tight.
READ_POLL_INTERVAL_SECONDS = 0.25
READ_TIMEOUT_SECONDS = 15.0

# Mifare Classic 1K geometry: 16 sectors of 4 blocks of 16 bytes. Parsers index absolute
# offsets into the full image (sector trailers included), so these are what reassembling a
# sector-by-sector read has to reproduce exactly.
SECTORS_PER_CLASSIC_1K = 16
BYTES_PER_CLASSIC_SECTOR = 64
CLASSIC_1K_SIZE_BYTES = SECTORS_PER_CLASSIC_1K * BYTES_PER_CLASSIC_SECTOR


class TagReadResult(object):
    """Outcome of one raw read.

    `retryable` mirrors OpenRFID's (data, retryable) contract and is the only field the
    dispatch branches on: True means the tag moved or vanished and another attempt may
    work, False means this format/key combination is simply wrong. The firmware's `error`
    strings are diagnostics only - they are not part of the contract and may be reworded.
    """

    def __init__(
        self,
        data=None,
        retryable=False,
        error=None,
        uid=None,
        tagType=None,
        ntagVariant=None,
        durationMs=None,
        authFailedSectors=None,
    ):
        self.data = data
        self.retryable = retryable
        self.error = error
        self.uid = uid
        self.tagType = tagType
        self.ntagVariant = ntagVariant
        self.durationMs = durationMs
        self.authFailedSectors = authFailedSectors

    @property
    def ok(self):
        return self.data is not None

    def toDiagnostics(self):
        return {
            "error": self.error,
            "retryable": self.retryable,
            "uid": self.uid,
            "tagType": self.tagType,
            "ntagVariant": self.ntagVariant,
            "durationMs": self.durationMs,
            "authFailedSectors": self.authFailedSectors,
        }


class OctoScaleTagReader(object):
    def __init__(self, callOctoScale, baseUrl, logger, sleep=None):
        # callOctoScale(baseUrl, path, timeout=None, method="GET", json=None)
        #   -> (response, errorMessage), never raises
        self._call = callOctoScale
        self._baseUrl = baseUrl
        self._logger = logger
        self._sleep = sleep if sleep is not None else time.sleep

    def probe(self):
        """ScanResult for the tag currently on the reader, or None."""
        response, errorMessage = self._call(self._baseUrl, "/nfcprobe")
        if errorMessage is not None or response is None:
            return None
        try:
            probeData = response.json()
        except ValueError:
            return None
        if probeData.get("present") is not True:
            return None

        uidHex = probeData.get("uid")
        try:
            uid = bytes.fromhex(uidHex) if uidHex else b""
        except ValueError:
            uid = b""

        return ScanResult(
            tagTypeFromOctoScale(probeData.get("tagType")),
            uid,
            sak=probeData.get("sak"),
            atqa=probeData.get("atqa"),
        )

    def readRaw(self, keyA=None, keyB=None, sectors=None):
        """Start a read job and poll it to completion. Returns a TagReadResult."""
        requestBody = {}
        if keyA is not None:
            requestBody["keyA"] = keyA
        if keyB is not None:
            # Only sent when a parser actually needs it: after a failed key A auth the card
            # drops out of the selected state and has to be re-selected before every key B
            # attempt. Measured across 16 sectors: 765 ms for key A alone, 2547 ms with key
            # B added - 3.3x, so sending it "just in case" triples every rejection.
            requestBody["keyB"] = keyB
        if sectors is not None:
            # Reading only the sectors a parser needs is the single biggest saving:
            # ~54 ms for one sector versus ~765 ms for all sixteen on a rejection.
            requestBody["sectors"] = sectors

        response, errorMessage = self._call(
            self._baseUrl, "/nfcreadstart", method="POST", json=requestBody
        )
        if errorMessage is not None:
            # A 409 here means another RF job (write/dump/read) is in progress - transient.
            retryable = self._isBusyResponse(response)
            return TagReadResult(
                retryable=retryable, error=self._errorFrom(response, errorMessage)
            )

        return self._pollUntilDone()

    def readRawPerSector(self, keys, sectors=None):
        """Read a Classic tag whose sectors each need a different key.

        The firmware contract takes one key A per request, so a tag like Snapmaker - which
        derives a separate key for every sector from the tag UID - has to be read one sector
        at a time and reassembled here. The result is a full-size image with unread sectors
        left as zeroes, so parsers keep indexing absolute offsets into a 1K dump and never
        have to know how the bytes were obtained.

        Costly by nature (one HTTP round-trip and one re-selection per sector), so it is
        used only when the keys actually differ - see the caller.
        """
        wanted = sectors if sectors is not None else list(range(SECTORS_PER_CLASSIC_1K))

        image = bytearray(CLASSIC_1K_SIZE_BYTES)
        readAny = False
        lastResult = None
        failedSectors = []

        for sector in wanted:
            if sector < 0 or sector >= len(keys):
                continue
            result = self.readRaw(keyA=keys[sector], sectors=[sector])
            lastResult = result
            if not result.ok or not result.data:
                failedSectors.append(sector)
                continue

            # The firmware answers with a full-size image and zeroes what it could not read,
            # so only this sector's own bytes may be copied out of it - taking the whole
            # response would overwrite sectors already gathered.
            start = sector * BYTES_PER_CLASSIC_SECTOR
            end = start + BYTES_PER_CLASSIC_SECTOR
            chunk = result.data[start:end]
            if len(chunk) < BYTES_PER_CLASSIC_SECTOR:
                failedSectors.append(sector)
                continue
            image[start:end] = chunk
            readAny = True

        if not readAny:
            # Every sector refused the derived keys: this is not that vendor's tag.
            return TagReadResult(
                retryable=(lastResult.retryable if lastResult is not None else False),
                error=(
                    lastResult.error if lastResult is not None else "authentication failed"
                ),
                uid=(lastResult.uid if lastResult is not None else None),
                tagType="mifareClassic1k",
            )

        return TagReadResult(
            data=bytes(image),
            uid=(lastResult.uid if lastResult is not None else None),
            tagType="mifareClassic1k",
            authFailedSectors=failedSectors or None,
        )

    def _pollUntilDone(self):
        waited = 0.0
        while waited < READ_TIMEOUT_SECONDS:
            response, errorMessage = self._call(self._baseUrl, "/nfcreadstatus")
            if errorMessage is None and response is not None:
                try:
                    statusData = response.json()
                except ValueError:
                    statusData = None
                if statusData is not None and statusData.get("done") is True:
                    # The device self-clears "done" once it has been read, so this result
                    # must be consumed now - polling again would report nothing.
                    return self._resultFrom(statusData)
            self._sleep(READ_POLL_INTERVAL_SECONDS)
            waited += READ_POLL_INTERVAL_SECONDS

        return TagReadResult(
            retryable=True, error="OctoScale did not report a read result in time"
        )

    def _resultFrom(self, statusData):
        common = {
            "uid": statusData.get("uid"),
            "tagType": statusData.get("tagType"),
            "ntagVariant": statusData.get("ntagVariant"),
            "durationMs": statusData.get("durationMs"),
            "authFailedSectors": statusData.get("authFailedSectors"),
        }

        if statusData.get("ok") is not True:
            return TagReadResult(
                retryable=statusData.get("retryable") is True,
                error=statusData.get("error") or "Could not read the tag",
                **common
            )

        hexData = statusData.get("bytes") or ""
        try:
            data = bytes.fromhex(hexData)
        except ValueError:
            return TagReadResult(
                retryable=False, error="OctoScale sent unreadable tag data", **common
            )

        # Both of these guard against silently parsing the wrong bytes, which is worse than
        # not parsing at all: every ultralight parser indexes absolute offsets.
        byteCount = statusData.get("byteCount")
        if byteCount is not None and byteCount != len(data):
            return TagReadResult(
                retryable=False,
                error=(
                    "OctoScale reported "
                    + str(byteCount)
                    + " bytes but sent "
                    + str(len(data))
                ),
                **common
            )

        startPage = statusData.get("startPage")
        if startPage is not None and startPage != 0:
            # A dump that does not start at page 0 shifts every offset the parsers use.
            # Refusing loudly beats handing them data that looks plausible.
            if self._logger is not None:
                self._logger.error(
                    "OctoScale returned a tag dump starting at page %s instead of 0 - "
                    "refusing to parse it, every vendor parser assumes absolute offsets",
                    startPage,
                )
            return TagReadResult(
                retryable=False,
                error="OctoScale returned a dump that does not start at page 0",
                **common
            )

        return TagReadResult(data=data, retryable=False, **common)

    def _isBusyResponse(self, response):
        body = self._bodyOf(response)
        if body is None:
            return False
        return body.get("retryable") is True

    def _errorFrom(self, response, fallbackMessage):
        body = self._bodyOf(response)
        if body is None:
            return fallbackMessage
        return body.get("message") or body.get("error") or fallbackMessage

    def _bodyOf(self, response):
        if response is None:
            return None
        try:
            body = response.json()
        except (ValueError, AttributeError):
            return None
        return body if isinstance(body, dict) else None
