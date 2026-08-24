# coding=utf-8

# Ported from OpenRFID (https://github.com/suchmememanyskill/OpenRFID), GPL-3.0,
# src/tag/binary.py. Combined into this AGPLv3 work; see THIRD_PARTY_NOTICES.md and
# 3rdPartySoftware/OpenRFID/LICENSE.
#
# Divergences from upstream:
#  - every helper takes a length guard: upstream indexes fixed offsets unchecked, which
#    raises IndexError (and would surface as an HTTP 500) as soon as a tag turns out
#    shorter than a parser expects - an NTAG213 where a parser assumed a 215, say. Here a
#    short read returns None so the parser can reject the tag the same way it rejects a
#    wrong magic number.

from __future__ import annotations

import struct


def extract_slice(data, pos, length):
    """Bytes at pos, or None when the data is too short - never a truncated slice.

    Python would happily return fewer bytes than asked for, which is how a short tag turns
    into a wrong parse instead of a rejection. Parsers use this directly to check magic
    numbers.
    """
    if data is None or pos < 0 or length < 0:
        return None
    if pos + length > len(data):
        return None
    return data[pos : pos + length]


# Internal alias kept so the helpers below read like upstream's.
_slice = extract_slice


def extract_string(data, pos, length):
    """Extract a null-terminated ASCII string from the data, or None if out of range."""
    raw = _slice(data, pos, length)
    if raw is None:
        return None
    return raw.decode("ascii", errors="ignore").rstrip("\x00")


def extract_uint16_le(data, pos):
    """Extract a little-endian uint16, or None if out of range."""
    raw = _slice(data, pos, 2)
    if raw is None:
        return None
    return struct.unpack("<H", raw)[0]


def extract_uint16_be(data, pos):
    """Extract a big-endian uint16, or None if out of range."""
    raw = _slice(data, pos, 2)
    if raw is None:
        return None
    return struct.unpack(">H", raw)[0]


def extract_uint32_le(data, pos):
    """Extract a little-endian uint32, or None if out of range."""
    raw = _slice(data, pos, 4)
    if raw is None:
        return None
    return struct.unpack("<I", raw)[0]


def extract_uint32_be(data, pos):
    """Extract a big-endian uint32, or None if out of range."""
    raw = _slice(data, pos, 4)
    if raw is None:
        return None
    return struct.unpack(">I", raw)[0]


def extract_float_le(data, pos):
    """Extract a little-endian float, or None if out of range."""
    raw = _slice(data, pos, 4)
    if raw is None:
        return None
    return struct.unpack("<f", raw)[0]


def extract_byte(data, pos):
    """Single byte at pos, or None if out of range."""
    raw = _slice(data, pos, 1)
    if raw is None:
        return None
    return raw[0]
