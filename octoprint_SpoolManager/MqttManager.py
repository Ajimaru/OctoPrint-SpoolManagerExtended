# coding=utf-8

import re
import socket

from octoprint_SpoolManager.common.EventBusKeys import EventBusKeys
from octoprint_SpoolManager.common.SettingsKeys import SettingsKeys

# Read-only MQTT support: publishes the selected spool per tool as retained state topics
# and (optionally) Home Assistant autodiscovery configs via the OctoPrint-MQTT plugin.
# There is deliberately NO mqtt_subscribe usage - the plugin cannot be controlled via MQTT.
class MqttManager(object):

	# object-id -> (display name, extra discovery config attributes)
	SENSOR_DEFINITIONS = {
		"spool_name": ("Spool", {
			"value_template": "{{ value_json.spoolName if value_json.spoolName else 'none' }}",
			"icon": "mdi:printer-3d-nozzle",
		}),
		"material": ("Material", {
			"value_template": "{{ value_json.material if value_json.material else 'none' }}",
			"icon": "mdi:dna",
		}),
		"color_name": ("Color", {
			"value_template": "{{ value_json.colorName if value_json.colorName else 'none' }}",
			"icon": "mdi:palette",
		}),
		"remaining_weight": ("Remaining weight", {
			"value_template": "{{ value_json.remainingWeight }}",
			"unit_of_measurement": "g",
			"state_class": "measurement",
			"icon": "mdi:weight-gram",
		}),
	}

	STATE_PAYLOAD_KEYS = ["spoolName", "material", "colorName", "remainingWeight", "databaseId"]

	def __init__(self, plugin, logger):
		self._plugin = plugin
		self._logger = logger
		self._mqtt_publish = None
		self._lastPublishedPayloads = {}
		self._lastDiscoveryIdentity = None  # (prefix, base, instance, toolCount) of last published discovery

	def initialize(self):
		helpers = self._plugin._plugin_manager.get_helpers("mqtt", "mqtt_publish")
		if helpers and "mqtt_publish" in helpers:
			self._mqtt_publish = helpers["mqtt_publish"]
			self._logger.info("MQTT plugin helper found, MQTT publishing available")
		else:
			self._mqtt_publish = None
			self._logger.info("MQTT plugin not available, SpoolManager MQTT support disabled")

	################################################################################################ settings accessors

	def _isEnabled(self):
		return self._plugin._settings.get_boolean([SettingsKeys.SETTINGS_KEY_MQTT_ENABLED])

	def isOperational(self):
		return self._mqtt_publish is not None and self._isEnabled()

	def isMqttPluginAvailable(self):
		return self._mqtt_publish is not None

	def _isDiscoveryEnabled(self):
		return self._plugin._settings.get_boolean([SettingsKeys.SETTINGS_KEY_MQTT_DISCOVERY_ENABLED])

	def _getRetain(self):
		return self._plugin._settings.get_boolean([SettingsKeys.SETTINGS_KEY_MQTT_RETAIN])

	def _getDiscoveryPrefix(self):
		prefix = self._plugin._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_DISCOVERY_PREFIX])
		return (prefix or "homeassistant").strip().strip("/")

	def _getTopicBase(self):
		base = self._plugin._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_TOPIC_BASE])
		return (base or "octoprint/plugin/SpoolManager").strip().strip("/")

	@staticmethod
	def _slugify(value):
		return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_") or "octoprint"

	def _getInstanceName(self):
		instanceName = self._plugin._settings.get([SettingsKeys.SETTINGS_KEY_MQTT_INSTANCE_NAME])
		if not instanceName or not instanceName.strip():
			instanceName = self._plugin._settings.global_get(["appearance", "name"])
		if not instanceName or not instanceName.strip():
			instanceName = socket.gethostname()
		return self._slugify(instanceName)

	def _getToolCount(self):
		printerProfile = self._plugin._printer_profile_manager.get_current_or_default()
		try:
			profileToolCount = printerProfile["extruder"]["count"]
		except (KeyError, TypeError):
			profileToolCount = 1
		selectedIds = self._plugin._settings.get([SettingsKeys.SETTINGS_KEY_SELECTED_SPOOLS_DATABASE_IDS]) or []
		return max(profileToolCount, len(selectedIds), 1)

	############################################################################################ topic/payload building

	def _stateTopic(self, topicBase, instanceName, toolIndex):
		return "%s/%s/tool%d" % (topicBase, instanceName, toolIndex)

	def _discoveryTopic(self, discoveryPrefix, instanceName, toolIndex, objectId):
		return "%s/sensor/spoolmanager_%s/tool%d_%s/config" % (discoveryPrefix, instanceName, toolIndex, objectId)

	def _buildStatePayload(self, instanceName, toolIndex, eventPayload):
		statePayload = {key: None for key in self.STATE_PAYLOAD_KEYS}
		if eventPayload is not None:
			for key in self.STATE_PAYLOAD_KEYS:
				if key in eventPayload:
					statePayload[key] = eventPayload[key]
		remainingWeight = statePayload.get("remainingWeight")
		if remainingWeight is not None:
			try:
				statePayload["remainingWeight"] = round(float(remainingWeight), 1)
			except (TypeError, ValueError):
				statePayload["remainingWeight"] = None
		statePayload["toolIndex"] = toolIndex
		statePayload["instance"] = instanceName
		return statePayload

	def _buildDiscoveryPayload(self, topicBase, instanceName, toolIndex, objectId):
		displayName, extraConfig = self.SENSOR_DEFINITIONS[objectId]
		stateTopic = self._stateTopic(topicBase, instanceName, toolIndex)
		configPayload = {
			"name": "Tool %d %s" % (toolIndex, displayName),
			"unique_id": "spoolmanager_%s_tool%d_%s" % (instanceName, toolIndex, objectId),
			"state_topic": stateTopic,
			"availability": [{
				"topic": stateTopic,
				"value_template": "{{ 'online' if value_json.databaseId is not none else 'offline' }}",
			}],
			"device": {
				"identifiers": ["spoolmanager_%s" % instanceName],
				"name": "SpoolManager (%s)" % instanceName,
				"manufacturer": "OctoPrint",
				"model": "OctoPrint-SpoolManager",
				"sw_version": self._plugin._plugin_version,
			},
		}
		configPayload.update(extraConfig)
		return configPayload

	def _publish(self, topic, payload, force=False, retained=None):
		if self._mqtt_publish is None:
			return
		if retained is None:
			retained = self._getRetain()
		cacheKey = topic
		cacheValue = str(payload)
		if not force and self._lastPublishedPayloads.get(cacheKey) == cacheValue:
			return
		try:
			self._mqtt_publish(topic, payload, retained=retained, allow_queueing=True)
			self._lastPublishedPayloads[cacheKey] = cacheValue
		except Exception:
			self._logger.exception("Could not publish to MQTT topic '%s'" % topic)

	##################################################################################################### public actions

	def publishDiscovery(self):
		if not self.isOperational() or not self._isDiscoveryEnabled():
			return
		discoveryPrefix = self._getDiscoveryPrefix()
		topicBase = self._getTopicBase()
		instanceName = self._getInstanceName()
		toolCount = self._getToolCount()
		for toolIndex in range(toolCount):
			for objectId in self.SENSOR_DEFINITIONS:
				topic = self._discoveryTopic(discoveryPrefix, instanceName, toolIndex, objectId)
				# discovery configs are always retained, otherwise HA loses them on restart;
				# force bypasses the publish-cache, so a settings-save re-publishes even if the
				# broker silently dropped an earlier attempt (e.g. ACL problems)
				self._publish(topic, self._buildDiscoveryPayload(topicBase, instanceName, toolIndex, objectId), force=True, retained=True)
		self._lastDiscoveryIdentity = (discoveryPrefix, topicBase, instanceName, toolCount)
		self._logger.info("Published MQTT discovery configs for %d tool(s) as instance '%s'" % (toolCount, instanceName))

	def publishToolState(self, toolIndex, eventPayload):
		if not self.isOperational():
			return
		instanceName = self._getInstanceName()
		topic = self._stateTopic(self._getTopicBase(), instanceName, toolIndex)
		self._publish(topic, self._buildStatePayload(instanceName, toolIndex, eventPayload))

	def publishAllStates(self):
		if not self.isOperational():
			return
		try:
			selectedSpools = self._plugin.loadSelectedSpools()
		except Exception:
			self._logger.exception("Could not load selected spools for MQTT publishing")
			selectedSpools = []
		toolCount = max(self._getToolCount(), len(selectedSpools))
		for toolIndex in range(toolCount):
			spoolModel = selectedSpools[toolIndex] if toolIndex < len(selectedSpools) else None
			eventPayload = None
			if spoolModel is not None:
				eventPayload = {
					"databaseId": spoolModel.databaseId,
					"spoolName": spoolModel.displayName,
					"material": spoolModel.material,
					"colorName": spoolModel.colorName,
					"remainingWeight": spoolModel.remainingWeight,
				}
			self.publishToolState(toolIndex, eventPayload)

	def handleEvent(self, eventKey, eventPayload):
		if not self.isOperational():
			return
		if eventKey in (EventBusKeys.EVENT_BUS_SPOOL_SELECTED, EventBusKeys.EVENT_BUS_SPOOL_WEIGHT_UPDATED_AFTER_PRINT):
			toolIndex = eventPayload.get("toolId")
			if toolIndex is not None:
				self.publishToolState(toolIndex, eventPayload)
		elif eventKey == EventBusKeys.EVENT_BUS_SPOOL_DESELECTED:
			toolIndex = eventPayload.get("toolId")
			if toolIndex is not None:
				self.publishToolState(toolIndex, None)
		# spool_added / spool_deleted are not tool related -> nothing to publish

	def clearRetainedTopics(self, discoveryPrefix=None, topicBase=None, instanceName=None, toolCount=None):
		if self._mqtt_publish is None:
			return
		if self._lastDiscoveryIdentity is not None:
			lastPrefix, lastBase, lastInstance, lastToolCount = self._lastDiscoveryIdentity
			discoveryPrefix = discoveryPrefix or lastPrefix
			topicBase = topicBase or lastBase
			instanceName = instanceName or lastInstance
			toolCount = toolCount or lastToolCount
		discoveryPrefix = discoveryPrefix or self._getDiscoveryPrefix()
		topicBase = topicBase or self._getTopicBase()
		instanceName = instanceName or self._getInstanceName()
		toolCount = toolCount or self._getToolCount()
		for toolIndex in range(toolCount):
			for objectId in self.SENSOR_DEFINITIONS:
				# empty retained payload removes the discovered entity (HA convention)
				self._publish(self._discoveryTopic(discoveryPrefix, instanceName, toolIndex, objectId), "", force=True, retained=True)
			self._publish(self._stateTopic(topicBase, instanceName, toolIndex), "", force=True, retained=True)
		self._lastPublishedPayloads = {}
		self._lastDiscoveryIdentity = None
		self._logger.info("Cleared retained MQTT topics for instance '%s'" % instanceName)
