# coding=utf-8

import json
import threading
import time

from octoprint_SpoolManager.common.SettingsKeys import SettingsKeys

try:
    import websocket  # provided by websocket-client
except ImportError:  # pragma: no cover - dependency is declared in setup.py
    websocket = None


# Snapmaker U1 as a self-reporting RFID reader.
#
# The U1 running the paxx12 Extended Firmware exposes a Moonraker-compatible JSON-RPC
# API. Its `filament_detect` object holds the per-channel RFID state, so the printer can
# tell us which spool sits in which of its channels - channel N maps to tool N of THIS
# OctoPrint instance. It is deliberately NOT a hand scanner for other printers: a spool
# physically sits in the U1, so it belongs to the U1. OctoScale covers the hand-scanner
# case.
#
# The host is never configured by hand - it is derived from the MoonrakerConnector's
# active connection, the same way the connector's own _get_connector_params() does it.
#
# Read-only: this only ever selects a spool, it never writes weights or filament data
# back to the printer.


# Fields of filament_detect.info[channel] that carry filament metadata. Used to prefill
# the wizard/edit dialog when a tag is unknown. `CARD_UID`/`CARD_TYPE` are orthogonal -
# they say a tag is physically present, not what it contains.
_TAG_METADATA_FIELDS = [
    "VENDOR",
    "MANUFACTURER",
    "MAIN_TYPE",
    "SUB_TYPE",
    "RGB_1",
    "RGB_2",
    "RGB_3",
    "RGB_4",
    "RGB_5",
    "ALPHA",
    "COLOR_NUMS",
    "HOTEND_MIN_TEMP",
    "HOTEND_MAX_TEMP",
    "BED_TEMP",
    "WEIGHT",
    "OFFICIAL",
    "CARD_TYPE",
]

# Values the firmware uses for "not set". Prefilling those would write `0 °C` or a vendor
# literally called "NONE" into a spool, which is worse than leaving the field empty.
_EMPTY_STRING_VALUES = {"", "NONE", "None", "none"}


def normalizeCardUid(cardUid):
    """
    Turns the firmware's CARD_UID into a stable hex string ("A1B2C3D4").

    CARD_UID is a *byte array* (list[int]) per the firmware spec - an empty channel
    reports plain `0`, which makes it look like an integer, so both shapes are handled.
    This is the single place UID normalization happens: lookup and teaching must produce
    identical strings or nothing will ever match.

    Returns None when no tag is present.
    """
    if cardUid is None:
        return None

    # list/tuple of byte ints - the documented shape
    if isinstance(cardUid, (list, tuple)):
        byteValues = []
        for entry in cardUid:
            try:
                byteValues.append(int(entry) & 0xFF)
            except (TypeError, ValueError):
                return None
        if not byteValues or all(value == 0 for value in byteValues):
            return None
        return "".join("%02X" % value for value in byteValues)

    # defensive: a firmware build reporting a plain integer
    if isinstance(cardUid, int):
        if cardUid == 0:
            return None
        hexValue = "%X" % cardUid
        if len(hexValue) % 2:
            hexValue = "0" + hexValue
        return hexValue

    if isinstance(cardUid, str):
        cleaned = cardUid.strip().replace(":", "").replace("-", "").upper()
        if not cleaned or all(character == "0" for character in cleaned):
            return None
        return cleaned

    return None


def extractTagMetadata(channelInfo):
    """
    Picks the filament metadata out of one filament_detect.info entry, dropping values
    the firmware uses as "unset" so they never end up prefilled.
    """
    metadata = {}
    if not isinstance(channelInfo, dict):
        return metadata

    for fieldName in _TAG_METADATA_FIELDS:
        if fieldName not in channelInfo:
            continue
        value = channelInfo[fieldName]
        if value is None:
            continue
        if isinstance(value, str) and value.strip() in _EMPTY_STRING_VALUES:
            continue
        # numeric 0 means "not set" for temperatures/weight, but is meaningful for
        # ALPHA (fully transparent) and COLOR_NUMS
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value == 0
            and fieldName not in ("ALPHA", "COLOR_NUMS")
        ):
            continue
        metadata[fieldName] = value
    return metadata


class U1RfidManager(object):

    # detection chain stages, in order - the UI shows which one failed
    STAGE_CONNECTOR = "connector"
    STAGE_MOONRAKER = "moonraker"
    STAGE_PRINTER = "printer"
    STAGE_FILAMENT_DETECT = "filamentDetect"

    RECONNECT_MIN_SECONDS = 2
    RECONNECT_MAX_SECONDS = 60

    def __init__(self, plugin, logger):
        self._plugin = plugin
        self._logger = logger

        self._thread = None
        self._stopEvent = threading.Event()
        self._websocket = None
        self._lock = threading.RLock()

        # last CARD_UID seen per channel, for 0 -> UID edge detection
        self._lastUidByChannel = {}
        # last *unknown* UID per channel, so the user can pick it up later
        self._unknownTagByChannel = {}

        self._connected = False
        self._lastError = None
        self._requestId = 0

        # detection chain result, refreshed on every evaluation
        self._chain = {
            self.STAGE_CONNECTOR: False,
            self.STAGE_MOONRAKER: False,
            self.STAGE_PRINTER: False,
            self.STAGE_FILAMENT_DETECT: False,
        }
        self._chainMessage = "Not checked yet"
        self._printerInfo = {}
        self._activeHost = None
        self._activePort = None

    ################################################################################################ lifecycle

    def initialize(self):
        if websocket is None:
            self._lastError = (
                "Python package 'websocket-client' is missing, U1 RFID support disabled"
            )
            self._logger.warning(self._lastError)
            return
        self.refresh()

    def shutdown(self):
        self._stopEvent.set()
        self._closeSocket()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None

    def refresh(self):
        """
        Re-evaluates the detection chain and starts/stops the reader accordingly. Called
        on startup, after settings changes and whenever the printer connection changes -
        without this a connection switch would leave the reader talking to the old host.
        """
        self.evaluateDetectionChain()

        shouldRun = self._isEnabled() and self.isSupported()
        if shouldRun:
            self._start()
        else:
            self._stop()

    ################################################################################################ settings accessors

    def _isEnabled(self):
        return self._plugin._settings.get_boolean(
            [SettingsKeys.SETTINGS_KEY_U1RFID_ENABLED]
        )

    ################################################################################################ detection chain

    def _getConnectorParams(self):
        """
        Host/port of the active Moonraker printer, or None.

        Mirrors OctoPrint-MoonrakerConnector's own _get_connector_params(): read
        `connection_state` off the printer and check its `connector` field. Needs
        OctoPrint 2.0 (ConnectedPrinter); on 1.x there is no connection_state and the
        chain simply stops at the connector stage.
        """
        printer = getattr(self._plugin, "_printer", None)
        if printer is None:
            return None
        connectionState = getattr(printer, "connection_state", None)
        if connectionState is None:
            return None
        if callable(connectionState):
            try:
                connectionState = connectionState()
            except Exception:
                return None
        if not isinstance(connectionState, dict):
            return None
        if connectionState.get("connector") != "moonraker":
            return None
        host = connectionState.get("host")
        if not host:
            return None
        port = connectionState.get("port") or 7125
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 7125
        return {"host": host, "port": port, "apikey": connectionState.get("apikey")}

    def _isConnectorPluginAvailable(self):
        pluginManager = getattr(self._plugin, "_plugin_manager", None)
        if pluginManager is None:
            return False
        plugins = getattr(pluginManager, "plugins", None) or {}
        connector = plugins.get("moonraker_connector")
        if connector is None:
            return False
        return getattr(connector, "enabled", True)

    def evaluateDetectionChain(self):
        """
        Runs the four detection stages. Capability decides, model only informs: the
        feature unlocks on `filament_detect` being present, so U1-like hardware and
        future models keep working. Never a firmware version check - the paxx12 version
        is not exposed over the API at all.
        """
        chain = {
            self.STAGE_CONNECTOR: False,
            self.STAGE_MOONRAKER: False,
            self.STAGE_PRINTER: False,
            self.STAGE_FILAMENT_DETECT: False,
        }
        message = ""
        printerInfo = {}
        host = None
        port = None

        if websocket is None:
            self._applyChain(
                chain, "Python package 'websocket-client' is missing", printerInfo, None, None
            )
            return

        if not self._isConnectorPluginAvailable():
            message = "MoonrakerConnector plugin not installed or disabled (requires OctoPrint 2.0)"
            self._applyChain(chain, message, printerInfo, None, None)
            return
        chain[self.STAGE_CONNECTOR] = True

        params = self._getConnectorParams()
        if params is None:
            message = "The active printer is not a Moonraker printer"
            self._applyChain(chain, message, printerInfo, None, None)
            return
        chain[self.STAGE_MOONRAKER] = True
        host = params["host"]
        port = params["port"]

        printerInfo = self._fetchPrinterInfo(host, port)
        if printerInfo:
            chain[self.STAGE_PRINTER] = True

        hasFilamentDetect = self._probeFilamentDetect(host, port)
        if not hasFilamentDetect:
            message = (
                "Printer does not expose 'filament_detect' - RFID reading requires the "
                "Extended Firmware"
            )
            self._applyChain(chain, message, printerInfo, host, port)
            return
        chain[self.STAGE_FILAMENT_DETECT] = True

        self._applyChain(chain, "", printerInfo, host, port)

    def _applyChain(self, chain, message, printerInfo, host, port):
        with self._lock:
            self._chain = chain
            self._chainMessage = message
            self._printerInfo = printerInfo or {}
            self._activeHost = host
            self._activePort = port

    def _httpGet(self, host, port, path, timeoutSeconds=5):
        import requests

        url = "http://%s:%s%s" % (host, port, path)
        try:
            response = requests.get(url, timeout=timeoutSeconds)
            if response.status_code != 200:
                return None
            return response.json()
        except Exception as exception:
            self._logger.debug("U1 RFID: GET %s failed: %s" % (url, exception))
            return None

    def _fetchPrinterInfo(self, host, port):
        """Model/name/firmware for the settings status - informative, never a gate."""
        payload = self._httpGet(host, port, "/machine/system_info")
        if not payload:
            return {}
        productInfo = (payload.get("result") or {}).get("system_info", {}).get(
            "product_info", {}
        )
        if not isinstance(productInfo, dict):
            return {}
        return {
            "machineType": productInfo.get("machine_type"),
            "deviceName": productInfo.get("device_name"),
            "firmwareVersion": productInfo.get("firmware_version"),
        }

    def _probeFilamentDetect(self, host, port):
        payload = self._httpGet(host, port, "/printer/objects/list")
        if not payload:
            return False
        objects = (payload.get("result") or {}).get("objects") or []
        return "filament_detect" in objects

    ################################################################################################ status for the UI

    def isSupported(self):
        with self._lock:
            return bool(self._chain.get(self.STAGE_FILAMENT_DETECT))

    def isOperational(self):
        return self._isEnabled() and self.isSupported()

    def getStatus(self):
        with self._lock:
            channels = []
            for channel in sorted(
                set(list(self._lastUidByChannel.keys()) + list(self._unknownTagByChannel.keys()))
            ):
                uid = self._lastUidByChannel.get(channel)
                unknown = self._unknownTagByChannel.get(channel)
                channels.append(
                    {
                        "channel": channel,
                        "uid": uid,
                        "unknownUid": (unknown or {}).get("uid"),
                        "spoolName": (unknown or {}).get("spoolName"),
                        "metadata": (unknown or {}).get("metadata", {}),
                    }
                )
            return {
                "enabled": self._isEnabled(),
                "supported": bool(self._chain.get(self.STAGE_FILAMENT_DETECT)),
                "connected": self._connected,
                "host": self._activeHost,
                "port": self._activePort,
                "chain": dict(self._chain),
                "chainMessage": self._chainMessage,
                "printerInfo": dict(self._printerInfo),
                "lastError": self._lastError,
                "channels": channels,
            }

    def getUnknownTags(self):
        with self._lock:
            return {
                str(channel): dict(entry)
                for channel, entry in self._unknownTagByChannel.items()
            }

    def testConnection(self):
        """
        Settings "Test connection" button: re-runs the chain and, on success, reports all
        channels with their tags so it is immediately visible whether tags are read at
        all. Short timeout, never blocks the UI.
        """
        self.evaluateDetectionChain()
        status = self.getStatus()
        if not status["supported"]:
            return {
                "ok": False,
                "message": status["chainMessage"] or "Printer not usable for RFID",
                "status": status,
            }

        payload = self._httpGet(
            status["host"],
            status["port"],
            "/printer/objects/query?filament_detect",
            timeoutSeconds=5,
        )
        if not payload:
            return {
                "ok": False,
                "message": "U1 at %s is not reachable" % status["host"],
                "status": status,
            }

        filamentDetect = (
            (payload.get("result") or {}).get("status", {}).get("filament_detect", {})
        )
        channels = []
        for index, channelInfo in enumerate(filamentDetect.get("info", []) or []):
            uid = normalizeCardUid((channelInfo or {}).get("CARD_UID"))
            spoolModel = self._findSpoolByUid(uid) if uid else None
            # run the same "unset" filtering the prefill uses, otherwise an empty channel
            # shows the firmware's literal "NONE" placeholders in the settings table
            metadata = extractTagMetadata(channelInfo)
            channels.append(
                {
                    "channel": index,
                    "uid": uid,
                    "cardType": metadata.get("CARD_TYPE"),
                    "vendor": metadata.get("VENDOR") or metadata.get("MANUFACTURER"),
                    "material": metadata.get("MAIN_TYPE"),
                    "spoolName": spoolModel.displayName if spoolModel else None,
                    "databaseId": spoolModel.databaseId if spoolModel else None,
                }
            )
        return {"ok": True, "message": "Connected", "status": status, "channels": channels}

    ################################################################################################ websocket reader

    def _start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopEvent.clear()
        self._thread = threading.Thread(
            target=self._run, name="SpoolManager-U1Rfid", daemon=True
        )
        self._thread.start()
        self._logger.info(
            "U1 RFID: reader started for %s:%s" % (self._activeHost, self._activePort)
        )

    def _stop(self):
        if self._thread is None:
            return
        self._logger.info("U1 RFID: stopping reader")
        self._stopEvent.set()
        self._closeSocket()
        self._thread = None

    def _closeSocket(self):
        socketRef = self._websocket
        self._websocket = None
        self._connected = False
        if socketRef is not None:
            try:
                socketRef.close()
            except Exception:
                pass

    def _run(self):
        backoff = self.RECONNECT_MIN_SECONDS
        while not self._stopEvent.is_set():
            host = self._activeHost
            port = self._activePort
            if not host:
                break
            try:
                self._connectAndListen(host, port)
                backoff = self.RECONNECT_MIN_SECONDS
            except Exception as exception:
                self._lastError = str(exception)
                self._logger.debug("U1 RFID: connection lost: %s" % exception)
            finally:
                self._closeSocket()

            if self._stopEvent.is_set():
                break
            # the U1 is not always on - back off instead of hammering it
            self._stopEvent.wait(backoff)
            backoff = min(backoff * 2, self.RECONNECT_MAX_SECONDS)

    def _connectAndListen(self, host, port):
        url = "ws://%s:%s/websocket" % (host, port)
        socketRef = websocket.create_connection(url, timeout=10)
        socketRef.settimeout(35)
        self._websocket = socketRef
        self._connected = True
        self._lastError = None
        self._logger.info("U1 RFID: connected to %s" % url)

        self._send(socketRef, "server.connection.identify", {
            "client_name": "OctoPrint-SpoolManager",
            "version": str(getattr(self._plugin, "_plugin_version", "")),
            "type": "agent",
            "url": "https://github.com/Ajimaru/OctoPrint-SpoolManager",
        })
        self._send(
            socketRef,
            "printer.objects.subscribe",
            {"objects": {"filament_detect": None}},
        )

        while not self._stopEvent.is_set():
            try:
                rawMessage = socketRef.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not rawMessage:
                break
            try:
                message = json.loads(rawMessage)
            except ValueError:
                continue
            self._handleMessage(message)

    def _send(self, socketRef, method, params):
        self._requestId += 1
        socketRef.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": self._requestId,
                }
            )
        )

    def _handleMessage(self, message):
        status = None
        if message.get("method") == "notify_status_update":
            params = message.get("params") or []
            if params and isinstance(params[0], dict):
                status = params[0]
        elif "result" in message:
            result = message.get("result") or {}
            status = result.get("status")

        if not isinstance(status, dict):
            return
        filamentDetect = status.get("filament_detect")
        if not isinstance(filamentDetect, dict):
            return
        infoList = filamentDetect.get("info")
        if not isinstance(infoList, list):
            return

        for index, channelInfo in enumerate(infoList):
            self._handleChannel(index, channelInfo)

    def _handleChannel(self, channel, channelInfo):
        uid = normalizeCardUid((channelInfo or {}).get("CARD_UID"))
        previousUid = self._lastUidByChannel.get(channel)

        if uid is None:
            # Tag gone: only forget the remembered state. No auto-deselect - the U1
            # briefly unloads during a filament change, which would otherwise clear a
            # perfectly valid selection.
            if previousUid is not None:
                self._lastUidByChannel.pop(channel, None)
            return

        if uid == previousUid:
            return

        self._lastUidByChannel[channel] = uid
        self._logger.info("U1 RFID: tag %s detected in channel %d" % (uid, channel))
        self._onTagDetected(channel, uid, channelInfo)

    ################################################################################################ spool resolution

    def _findSpoolByUid(self, uid):
        if not uid:
            return None
        try:
            return self._plugin._databaseManager.loadSpoolByCode(uid)
        except Exception as exception:
            self._logger.exception("U1 RFID: spool lookup failed: %s" % exception)
            return None

    def _onTagDetected(self, channel, uid, channelInfo):
        spoolModel = self._findSpoolByUid(uid)

        if spoolModel is None:
            metadata = extractTagMetadata(channelInfo)
            with self._lock:
                self._unknownTagByChannel[channel] = {
                    "uid": uid,
                    "channel": channel,
                    "metadata": metadata,
                    "timestamp": time.time(),
                }
            self._logger.info(
                "U1 RFID: tag %s (channel %d) is not assigned to any spool" % (uid, channel)
            )
            self._plugin._sendDataToClient(
                {
                    "action": "u1RfidUnknownTag",
                    "channel": channel,
                    "uid": uid,
                    "metadata": metadata,
                }
            )
            return

        with self._lock:
            self._unknownTagByChannel.pop(channel, None)

        # channel N -> tool N, always on this very instance
        result = self._plugin.selectSpoolForTool(channel, spoolModel.databaseId)
        self._plugin._sendDataToClient(
            {
                "action": "u1RfidSpoolSelected",
                "channel": channel,
                "uid": uid,
                "databaseId": spoolModel.databaseId,
                "spoolName": spoolModel.displayName,
                "status": result.get("status"),
                "toolIndex": result.get("toolIndex"),
            }
        )
