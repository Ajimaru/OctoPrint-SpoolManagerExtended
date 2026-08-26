# coding=utf-8

from peewee import (
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    FloatField,
    IntegerField,
    TextField,
)

from octoprint_SpoolManagerExtended.models.BaseModel import BaseModel


class SpoolModel(BaseModel):

    ########################################################
    ## SPOOL, MATERIAL, FILAMENT, PRINTER SETTINGS - FIELDS
    ########################################################

    ######################
    ## SPOOL - FIELDS
    ######################

    # version = IntegerField(null=True) # since V3, since V4: moved to BaseModel
    isActive = BooleanField(null=True)  # since V4
    isTemplate = BooleanField(null=True)
    displayName = CharField(null=True)
    vendor = CharField(null=True, index=True)  # since V4: added index
    # in g
    totalWeight = FloatField(null=True)
    spoolWeight = FloatField(null=True)  # since V3
    # in g
    usedWeight = FloatField(null=True)
    # in g
    remainingWeight = FloatField(null=True)

    # in mm
    totalLength = IntegerField(null=True)  # since V3
    usedLength = IntegerField(null=True)
    # Bar or QR Code
    code = CharField(null=True)
    # Manufacturer batch/lot number, shared by spools of the same production batch # since V8
    batchNumber = CharField(null=True)
    # Stable key for matching a U1 RFID tag back to this spool, derived from the last 4
    # hex chars of the tag's CARD_UID (see U1RfidManager.deriveRfidTagKey()). Deliberately
    # NOT the `code` field: a spool may carry its own independent barcode/serial there,
    # unrelated to what an RFID tag reports. since V10
    #
    # PRELIMINARY: Snapmaker spools carry two physical RFID tags (one per side), each
    # reporting a different CARD_UID. Live testing (4/4 spools) showed the last 4 hex
    # characters of CARD_UID are identical between both tags of the same physical spool -
    # this field exists to match on that stable suffix instead of the full, side-dependent
    # UID. Only 16 bits of key space (65536 values): a COLLISION IS POSSIBLE if many spools
    # of the same material/color/batch are registered, since two different physical spools
    # could end up with the same last-4-hex suffix by chance. Acceptable for typical
    # collection sizes; loadSpoolByRfidTagKey() resolves ties by newest match, and the
    # teach-in flow should warn on a pre-existing match rather than silently overwrite.
    rfidTagKey = CharField(null=True, index=True)

    firstUse = DateTimeField(null=True)
    lastUse = DateTimeField(null=True)

    purchasedFrom = CharField(null=True)
    purchasedOn = DateField(null=True)
    cost = FloatField(null=True)
    costUnit = CharField(
        null=True
    )  # deprecated needs to be removed, value should be used from pluginSettings

    labels = TextField(null=True)

    noteText = TextField(null=True)
    noteDeltaFormat = TextField(null=True)
    noteHtml = TextField(null=True)

    ######################
    ## MATERIAL - FIELDS
    ######################
    material = CharField(null=True, index=True)  # since V4: added index
    materialCharacteristic = CharField(
        null=True, index=True
    )  # strong, soft,... # since V4: new #TODO refactoring: list of predefined values
    density = FloatField(null=True)

    ######################
    ## FILAMENT - FIELDS
    ######################
    diameter = FloatField(null=True)
    diameterTolerance = FloatField(null=True)  # since V3
    colorName = CharField(null=True)
    color = CharField(null=True)
    finish = CharField(
        null=True
    )  # since V9: silk, matt, marble, metal, glow or custom text

    ######################
    ## PRINTER SETTINGS - FIELDS
    ######################
    flowRateCompensation = IntegerField(null=True)  # since V3
    # Temperature
    temperature = IntegerField(null=True)
    minTemperature = IntegerField(null=True)  # since V11
    maxTemperature = IntegerField(null=True)  # since V11
    bedTemperature = IntegerField(null=True)  # since V3
    minBedTemperature = IntegerField(null=True)  # since V11
    maxBedTemperature = IntegerField(null=True)  # since V11
    enclosureTemperature = IntegerField(
        null=True
    )  # since V3, V4 renamed from encloser to enclosure
    # Offset Temperature
    offsetTemperature = IntegerField(null=True)  # since V6
    offsetBedTemperature = IntegerField(null=True)  # since V6
    offsetEnclosureTemperature = IntegerField(null=True)  # since V6
    # Drying. Carried by most vendor RFID tags (Bambu, OpenSpool, SpoolEase, ...), which is
    # what these fields were added for - there was nowhere to put the values before.
    dryingTemperature = IntegerField(null=True)  # since V12
    dryingTime = IntegerField(null=True)  # since V12, in hours
    # Transmission Distance, used by HueForge and OrcaSlicer's full-spectrum mode. NOT a
    # length despite the name: the OpenPrintTag spec (main_fields.yaml key 27) defines it as
    # a dimensionless opacity number from 0.1 (most opaque) to 100 (most transparent).
    # Float, unlike the two above: it is a measured optical property, not a setpoint.
    td = FloatField(null=True)  # since V12
