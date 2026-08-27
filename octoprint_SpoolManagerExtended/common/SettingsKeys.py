# coding=utf-8


class SettingsKeys:

    SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS = "selectedSpoolsDatabaseIds"

    SETTINGS_KEY_REMINDER_SELECTING_SPOOL = "reminderSelectingSpool"
    SETTINGS_KEY_WARN_IF_SPOOL_NOT_SELECTED = "warnIfSpoolNotSelected"
    SETTINGS_KEY_WARN_IF_FILAMENT_NOT_ENOUGH = "warnIfFilamentNotEnough"

    ## Edit dialog: simple view mode (issue #1) - default view when no per-browser choice is stored
    SETTINGS_KEY_DEFAULT_VIEW_MODE_SIMPLE = "defaultViewModeSimple"

    SETTINGS_KEY_CURRENCY_SYMBOL = "currencySymbol"

    SETTINGS_KEY_SAFETY_LENGTH = "safetyLength"  # in mm e.g. ptfe-tube

    ## Display units for the edit spool dialog (values are always stored in mm/g)
    SETTINGS_KEY_LENGTH_UNIT = "lengthUnit"  # mm, cm, m
    SETTINGS_KEY_WEIGHT_UNIT = "weightUnit"  # g, kg

    ## Performance (Attribution @mdziekon, PR #8 / issue #5)
    SETTINGS_KEY_PERFORMANCE_LAZY_LOAD_SPOOL_SELECTOR_DATA = (
        "performanceLazyLoadSpoolSelectorData"
    )
    SETTINGS_KEY_PERFORMANCE_LAZY_LOAD_SPOOL_TABLE = "performanceLazyLoadSpoolTable"

    ## QR - Code
    SETTINGS_KEY_QR_CODE_ENABLED = "qrCodeEnabled"
    SETTINGS_KEY_QR_CODE_USE_URL_PREFIX = "qrCodeUseURLPrefix"
    SETTINGS_KEY_QR_CODE_URL_PREFIX = "qrCodeURLPrefix"
    SETTINGS_KEY_QR_CODE_FILL_COLOR = "qrCodeFillColor"
    SETTINGS_KEY_QR_CODE_BACKGROUND_COLOR = "qrCodeBackgroundColor"
    SETTINGS_KEY_QR_CODE_WIDTH = "qrCodeWidth"
    SETTINGS_KEY_QR_CODE_HEIGHT = "qrCodeHeight"
    SETTINGS_KEY_QR_CODE_LABEL_WIDTH_MM = "qrCodeLabelWidthMM"
    SETTINGS_KEY_QR_CODE_LABEL_HEIGHT_MM = "qrCodeLabelHeightMM"

    ## Export / Import
    SETTINGS_KEY_IMPORT_CSV_MODE = "importCSVMode"
    KEY_IMPORTCSV_MODE_REPLACE = "replace"
    KEY_IMPORTCSV_MODE_APPEND = "append"

    ## Storage
    SETTINGS_KEY_DATABASE_USE_EXTERNAL = "useExternal"
    SETTINGS_KEY_DATABASE_LOCAL_FILELOCATION = "databaseFileLocation"
    SETTINGS_KEY_DATABASE_TYPE = "databaseType"
    SETTINGS_KEY_DATABASE_HOST = "databaseHost"
    SETTINGS_KEY_DATABASE_PORT = "databasePort"
    SETTINGS_KEY_DATABASE_NAME = "databaseName"
    SETTINGS_KEY_DATABASE_USER = "databaseUser"
    SETTINGS_KEY_DATABASE_PASSWORD = "databasePassword"

    SETTINGS_KEY_TOOL_OFFSET_ENABLED = "toolOffsetEnabled"
    SETTINGS_KEY_BED_OFFSET_ENABLED = "bedOffsetEnabled"
    SETTINGS_KEY_ENCLOSURE_OFFSET_ENABLED = "enclosureOffsetEnabled"

    ## OctoScale (external scale + NFC writer device)
    SETTINGS_KEY_OCTOSCALE_ENABLED = "octoScaleEnabled"
    SETTINGS_KEY_OCTOSCALE_URL = "octoScaleUrl"
    # Which format an NFC-V/ISO15693 tag gets written in: "extended" (OctoScale's own
    # full-field layout, default) or "openSpool" (NDEF/JSON, phone-readable, fewer fields -
    # same tradeoff as NTAG's OpenSpool format). Global setting, not chosen per write -
    # Mifare Classic has no OpenSpool option (see TagFormats.py's module docstring for why).
    SETTINGS_KEY_OCTOSCALE_NFCV_FORMAT = "octoScaleNfcvFormat"
    # Which format an NTAG213/215/216 tag gets written in: "openSpool" (NDEF/JSON,
    # phone-readable, default/unchanged) or "extended" (OctoScale's own full-field binary
    # layout, mirrors the Mifare Classic/NFC-V extended format). Global setting, not chosen
    # per write. NTAG213 has no Extended option - the firmware rejects it outright (too
    # small), so the UI must warn rather than silently offer a write that always fails.
    SETTINGS_KEY_OCTOSCALE_NTAG_FORMAT = "octoScaleNtagFormat"
    # Reading vendor RFID tags (Bambu, Anycubic, Creality, ...) off a spool and offering to
    # create a spool from them. Off by default and deliberately opt-in: it reads formats
    # this project does not own, and a user who only ever writes their own tags should not
    # have it running. Note that the foreign-tag *write protection* is NOT gated on this -
    # that one is a safeguard against destroying a manufacturer tag and has to apply
    # whether or not reading is enabled.
    SETTINGS_KEY_OCTOSCALE_TAG_READING_ENABLED = "octoScaleTagReadingEnabled"
    # Writing over a tag the firmware has flagged as a manufacturer tag (occupancy
    # "foreign", see isConfirmedForeignTag in the frontend) - normally still possible after
    # the "this looks like a vendor tag, writing destroys it" confirmation. Off by default
    # and deliberately opt-in, mirroring SETTINGS_KEY_OCTOSCALE_TAG_READING_ENABLED above:
    # a user who wants a hard guarantee that their vendor tags are never touched, rather
    # than relying on always clicking "Cancel" on the confirmation, can disable the
    # possibility outright. Unlike the read setting, this does NOT gate the confirmation
    # dialog itself - the warning always shows when a foreign tag is on the reader; this
    # setting only removes "Overwrite anyway" as an option while it does.
    SETTINGS_KEY_OCTOSCALE_VENDOR_TAG_WRITE_ENABLED = "octoScaleVendorTagWriteEnabled"
    # Vendor keys the user supplies for tag formats whose sectors are protected by a
    # manufacturer secret (Bambu, Creality). A dict of {keyName: value}, empty by default -
    # NO KEY MATERIAL IS SHIPPED WITH THIS PLUGIN, and the parsers that need one stay
    # disabled until the user enters it. One dict rather than one setting per parser, the
    # same way the U1 integration deliberately has no per-device settings: whether a parser
    # runs follows from whether its key validates, not from a separate switch.
    #
    # Snapmaker is deliberately NOT here: its keys are derived from each tag's own UID with
    # salts that are published literals, so there is no secret to enter (see
    # common/FilamentTagKeys.py). Adding a field for it would be a field that does nothing.
    #
    # Listed in get_settings_restricted_paths() so it is not handed to unauthenticated
    # clients - see __init__.py.
    SETTINGS_KEY_OCTOSCALE_TAG_KEYS = "octoScaleTagKeys"

    ## Optional spool fields
    # Shows the TD (transmission distance) field in the spool dialog. Off by default: it is
    # a niche value used by HueForge and OrcaSlicer's full-spectrum mode, most users have no
    # way to measure it, and an always-visible field would just be one more empty box. The
    # value is still read from vendor tags and stored either way - this only controls
    # whether the field is offered for manual editing.
    SETTINGS_KEY_TD_FIELD_ENABLED = "tdFieldEnabled"

    ## SpoolmanDB-Community (optional remote temperature suggestions)
    SETTINGS_KEY_SPOOLMANDB_ENABLED = "spoolmanDbEnabled"
    SETTINGS_KEY_SPOOLMANDB_CACHE_TTL_DAYS = "spoolmanDbCacheTtlDays"

    ## TigerTag id lookup tables (material/brand/aspect/type/diameter/unit), auto-updated
    ## from TigerTag-SDK-Python at runtime - same mechanism as SpoolmanDB-Community above.
    ## Enabled by default: unlike SpoolmanDB (an opt-in convenience feature), these tables
    ## are needed for TigerTag reading/writing to resolve anything beyond the sparse
    ## offline fallback snapshot - see common/tagdata/tigertag_ids.json. No configurable
    ## TTL setting (unlike SpoolmanDB): the id tables change far less often than a filament
    ## price/spec database, so TigerTagIdService's fixed 7-day default is used throughout.
    SETTINGS_KEY_TIGERTAG_IDS_AUTO_UPDATE_ENABLED = "tigerTagIdsAutoUpdateEnabled"

    ## Debugging
    SETTINGS_KEY_SQL_LOGGING_ENABLED = "sqlLoggingEnabled"
    SETTINGS_KEY_EXTRUSION_DEBUGGING_ENABLED = "extrusionDebuggingEnabled"

    ## Snapmaker U1 RFID (self-reporting reader, read-only spool selection)
    # No host setting on purpose: the host is derived from the MoonrakerConnector's
    # active printer connection, see U1RfidManager.
    SETTINGS_KEY_U1RFID_ENABLED = "u1RfidEnabled"

    ## MQTT (read-only publishing via the OctoPrint-MQTT plugin)
    SETTINGS_KEY_MQTT_ENABLED = "mqttEnabled"
    SETTINGS_KEY_MQTT_DISCOVERY_ENABLED = "mqttDiscoveryEnabled"
    SETTINGS_KEY_MQTT_DISCOVERY_PREFIX = "mqttDiscoveryPrefix"
    SETTINGS_KEY_MQTT_TOPIC_BASE = "mqttTopicBase"
    SETTINGS_KEY_MQTT_INSTANCE_NAME = "mqttInstanceName"
    SETTINGS_KEY_MQTT_RETAIN = "mqttRetain"

    # Set when the user dismisses the migration hint. Anyone who was already running this
    # plugin when it was renamed still has a plugins.SpoolManager block, so "something is
    # migratable" stays true for them forever - they need a way to say no.
    SETTINGS_KEY_LEGACY_MIGRATION_DISMISSED = "legacyMigrationDismissed"
