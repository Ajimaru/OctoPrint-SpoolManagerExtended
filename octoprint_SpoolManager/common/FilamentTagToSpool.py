# coding=utf-8

# Maps a GenericFilament read off a vendor tag onto SpoolManager's own field names.
#
# The keys emitted here are deliberately the same flat camelCase names
# _buildFullSpoolPayload() uses (see TagFormats.py): the frontend's diff engine
# (OCTOSCALE_TAG_DIFF_FIELDS and friends in SpoolManager-OctoScale.js) already operates on
# exactly those, so emitting them means the before/after display works without a second
# implementation.

from __future__ import annotations

MAX_COLOR_SLOTS = 3


def _noneIfZero(value):
    # Several parsers hardcode 0 for "the tag does not carry this" (Qidi sets all four
    # temperatures to 0, Elegoo and TigerTag the bed temperature). Zero must never reach
    # the database as a real temperature - "not set" and "set to 0 C" are different things,
    # and only one of them is true here.
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric == 0:
        return None
    return value


def _colorCode(colors):
    """0xAARRGGBB ints -> "#RRGGBB", multi-color joined with ";" like the U1 path does."""
    if not colors:
        return None
    codes = []
    for argb in colors[:MAX_COLOR_SLOTS]:
        try:
            # Alpha is dropped: SpoolManager stores plain #RRGGBB.
            codes.append("#%06X" % (int(argb) & 0xFFFFFF))
        except (TypeError, ValueError):
            continue
    if not codes:
        return None
    return ";".join(codes)


def genericFilamentToSpoolFields(filament, uid=None):
    """Flat dict of SpoolManager field names, ready for the edit dialog / wizard.

    Only fields the tag actually carries are included - the frontend applies them with
    "never clear an existing value" semantics, so an absent key and an empty value have to
    stay distinguishable.
    """
    if filament is None:
        return {}

    fields = {}

    if filament.manufacturer:
        fields["vendor"] = filament.manufacturer
    if filament.type:
        fields["material"] = filament.type
    # Remaining modifiers (Silk, Matte, Translucent, ...) describe the variant, not the
    # base material - SpoolManager has a dedicated field for that, so they do not get
    # appended to the material name.
    if filament.modifiers:
        fields["materialCharacteristic"] = " ".join(
            [str(modifier) for modifier in filament.modifiers if modifier]
        ).strip() or None

    colorCode = _colorCode(filament.colors)
    if colorCode:
        fields["color"] = colorCode
    # colorName is deliberately not set: the frontend derives it from the color code, and
    # having two writers for that field has caused a real bug before (see applyToSpoolItem).

    if filament.diameter_mm:
        fields["diameter"] = filament.diameter_mm
    if filament.weight_grams:
        # The tag states the nominal amount of filament, not what is left on the spool.
        # remainingWeight is never set from a tag - it would be a guess.
        fields["totalWeight"] = filament.weight_grams

    minTemp = _noneIfZero(filament.hotend_min_temp_c)
    maxTemp = _noneIfZero(filament.hotend_max_temp_c)
    if minTemp is not None:
        fields["minTemperature"] = minTemp
    if maxTemp is not None:
        fields["maxTemperature"] = maxTemp
    # The single "temperature" field is what the printer actually uses; prefer the max and
    # fall back to the min, matching what applyToSpoolItem does for U1 tags.
    if maxTemp is not None or minTemp is not None:
        fields["temperature"] = maxTemp if maxTemp is not None else minTemp

    bedTemp = _noneIfZero(filament.bed_temp_c)
    if bedTemp is not None:
        # A tag carries one bed temperature; mirror it into the range so the spool is
        # consistent with how resolveTemperatureRange() reads it back.
        fields["bedTemperature"] = bedTemp
        fields["minBedTemperature"] = bedTemp
        fields["maxBedTemperature"] = bedTemp

    dryingTemp = _noneIfZero(filament.drying_temp_c)
    if dryingTemp is not None:
        fields["dryingTemperature"] = dryingTemp
    dryingTime = _noneIfZero(filament.drying_time_hours)
    if dryingTime is not None:
        fields["dryingTime"] = dryingTime
    td = _noneIfZero(filament.td)
    if td is not None:
        fields["td"] = td

    return fields


def diagnosticsFor(filament, uid=None, rfidTagKey=None):
    """Everything the tag said that is not a spool field - shown read-only, never stored."""
    if filament is None:
        return {}
    return {
        "parserId": filament.source_processor,
        # Identifies a filament *product*, not a physical spool: two identical spools share
        # it. Never use it to match a spool - that is what the tag UID is for.
        "uniqueId": filament.unique_id,
        "typeRecognized": filament.typeRecognized,
        "manufacturingDate": filament.manufacturing_date,
        "uid": uid,
        "rfidTagKey": rfidTagKey,
    }
