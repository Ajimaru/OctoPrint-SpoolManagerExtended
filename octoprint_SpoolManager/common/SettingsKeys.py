# coding=utf-8

class SettingsKeys():

	SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS = "selectedSpoolsDatabaseIds"

	SETTINGS_KEY_REMINDER_SELECTING_SPOOL = "reminderSelectingSpool"
	SETTINGS_KEY_WARN_IF_SPOOL_NOT_SELECTED = "warnIfSpoolNotSelected"
	SETTINGS_KEY_WARN_IF_FILAMENT_NOT_ENOUGH = "warnIfFilamentNotEnough"

	## Edit dialog: simple view mode (issue #1) - default view when no per-browser choice is stored
	SETTINGS_KEY_DEFAULT_VIEW_MODE_SIMPLE = "defaultViewModeSimple"

	SETTINGS_KEY_CURRENCY_SYMBOL = "currencySymbol"

	SETTINGS_KEY_SAFETY_LENGTH = "safetyLength" # in mm e.g. ptfe-tube

	## Display units for the edit spool dialog (values are always stored in mm/g)
	SETTINGS_KEY_LENGTH_UNIT = "lengthUnit" # mm, cm, m
	SETTINGS_KEY_WEIGHT_UNIT = "weightUnit" # g, kg

	## QR - Code
	SETTINGS_KEY_QR_CODE_ENABLED = "qrCodeEnabled"
	SETTINGS_KEY_QR_CODE_USE_URL_PREFIX = "qrCodeUseURLPrefix"
	SETTINGS_KEY_QR_CODE_URL_PREFIX = "qrCodeURLPrefix"
	SETTINGS_KEY_QR_CODE_FILL_COLOR = "qrCodeFillColor"
	SETTINGS_KEY_QR_CODE_BACKGROUND_COLOR = "qrCodeBackgroundColor"
	SETTINGS_KEY_QR_CODE_WIDTH = "qrCodeWidth"
	SETTINGS_KEY_QR_CODE_HEIGHT = "qrCodeHeight"

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

	## Debugging
	SETTINGS_KEY_SQL_LOGGING_ENABLED = "sqlLoggingEnabled"
	SETTINGS_KEY_EXTRUSION_DEBUGGING_ENABLED = "extrusionDebuggingEnabled"

	## MQTT (read-only publishing via the OctoPrint-MQTT plugin)
	SETTINGS_KEY_MQTT_ENABLED = "mqttEnabled"
	SETTINGS_KEY_MQTT_DISCOVERY_ENABLED = "mqttDiscoveryEnabled"
	SETTINGS_KEY_MQTT_DISCOVERY_PREFIX = "mqttDiscoveryPrefix"
	SETTINGS_KEY_MQTT_TOPIC_BASE = "mqttTopicBase"
	SETTINGS_KEY_MQTT_INSTANCE_NAME = "mqttInstanceName"
	SETTINGS_KEY_MQTT_RETAIN = "mqttRetain"
