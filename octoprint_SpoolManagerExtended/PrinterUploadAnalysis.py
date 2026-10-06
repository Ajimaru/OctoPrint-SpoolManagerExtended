# coding=utf-8

import copy
import io
import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime

from octoprint_SpoolManagerExtended.newodometer import NewFilamentOdometer


class TeeStream(io.RawIOBase):
    """
    Passes a stream through unchanged and writes a copy of everything read from it to
    `copyFile`. `onEnd(complete)` is called exactly once: with True when the source ran dry,
    with False when the stream was closed before that.
    """

    def __init__(self, source, copyFile, onEnd):
        super().__init__()
        self._source = source
        self._copyFile = copyFile
        self._onEnd = onEnd
        self._ended = False

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self._source.read(len(buffer))
        if not data:
            self._end(True)
            return 0
        buffer[: len(data)] = data
        self._copyFile.write(data)
        return len(data)

    def close(self):
        if not self.closed:
            try:
                self._source.close()
            except Exception:
                pass
            self._end(False)
        super().close()

    def _end(self, complete):
        if self._ended:
            return
        self._ended = True
        try:
            self._copyFile.close()
        finally:
            self._onEnd(complete)


class PrinterUploadAnalysis(object):
    """
    Remembers the filament usage of gcode files uploaded to a printer's own storage, for
    printers that cannot hand such a file back later - a serial printer's SD card: the
    serial connector has no download for it. The upload passes through OctoPrint, so a copy
    is taken on the way in (wrapUpload), run through the odometer in the background once
    FILE_ADDED says the file went to printer storage (onFileAdded), and the result is kept
    per file on the printer (filamentFor), across restarts.

    The copy is compared with nothing on the printer later: OctoPrint strips comments from
    every line it streams to an SD card (serial_comm's process_gcode_line), so the file
    there is smaller than the upload. A new upload under the same name replaces the entry,
    deleting the file removes it.
    """

    STORE_FILE_NAME = "printerUploadFilament.json"
    MAX_STORED_FILES = 200
    # how long a copy waits for the FILE_ADDED event that tells where its upload went
    PENDING_TIMEOUT_SECONDS = 600

    def __init__(
        self, logger, dataFolder, g90InfluencesExtruder=False, runInBackground=None
    ):
        self._logger = logger
        self._dataFolder = dataFolder
        self._g90InfluencesExtruder = g90InfluencesExtruder
        # runs the analysis off the event thread; tests pass a synchronous runner
        self._runInBackground = runInBackground or self._startThread
        self._lock = threading.Lock()
        # upload path (lower case) -> {"path", "copyPath", "complete", "startedAt"}
        self._pending = {}
        # file on the printer (lower case) -> entry, see _analyseAndStore(); loaded lazily
        self._store = None

    ############################################################################ upload side

    def wrapUpload(self, path, fileObject):
        """
        Takes a copy of an upload. Returns the file object to hand on to the storage:
        `fileObject` itself, or a wrapper that copies while the storage reads it.
        """
        self._dropStalePending()
        copyFile = tempfile.NamedTemporaryFile(
            prefix="spoolmanager-upload-", suffix=".gcode", delete=False
        )
        key = path.lower()
        entry = {
            "path": path,
            "copyPath": copyFile.name,
            "complete": False,
            "startedAt": time.time(),
        }
        with self._lock:
            replaced = self._pending.pop(key, None)
            self._pending[key] = entry
        if replaced is not None:
            self._removeCopy(replaced)

        diskPath = getattr(fileObject, "path", None)
        if diskPath is not None and os.path.isfile(diskPath):
            # already on disk (the usual case for an upload): copy it, the storage moves
            # the original away afterwards
            try:
                with copyFile:
                    with open(diskPath, "rb") as source:
                        shutil.copyfileobj(source, copyFile)
                entry["complete"] = True
            except Exception:
                self._logger.exception("Could not copy the upload '%s'" % path)
                self._forgetPending(key, entry)
            return fileObject

        def onEnd(complete):
            if complete:
                entry["complete"] = True
            else:
                self._forgetPending(key, entry)

        from octoprint.filemanager.util import StreamWrapper

        return StreamWrapper(
            fileObject.filename, TeeStream(fileObject.stream(), copyFile, onEnd)
        )

    def onFileAdded(self, storage, path):
        """
        FILE_ADDED: the copy of a file that went to printer storage is analysed, the copy
        of any other upload is dropped.
        """
        if path is None:
            return
        self._dropStalePending()
        with self._lock:
            entry = self._pending.pop(path.lower(), None)
            if entry is None and storage == "printer":
                # the printer may keep the file under another name than it was uploaded
                # as - the newest finished copy belongs to this upload
                entry = self._popNewestCompletePending()
        if entry is None:
            return
        if storage != "printer" or not entry["complete"]:
            self._removeCopy(entry)
            return

        self._runInBackground(self._analyseAndStore, path, entry)

    def onFileRemoved(self, storage, path):
        if storage != "printer" or path is None:
            return
        with self._lock:
            store = self._loadStore()
            if store.pop(path.lower(), None) is not None:
                self._saveStore(store)

    ############################################################################ lookup

    def filamentFor(self, path):
        """
        The per-tool filament usage analysed for a file on the printer, shaped like
        OctoPrint's analysis["filament"] without volumes: {"tool0": {"length": 1087.4}}.
        Tools the file does not use are absent. None when nothing is known.
        """
        if path is None:
            return None
        with self._lock:
            entry = self._loadStore().get(path.lower())
        if not entry or not entry.get("filament"):
            return None
        return copy.deepcopy(entry["filament"])

    ############################################################################ analysis

    def analyse(self, gcodePath):
        """Per-tool extrusion of a gcode file, counted the way a streamed print is."""
        odometer = NewFilamentOdometer()
        odometer.set_g90_extruder(self._g90InfluencesExtruder)
        with open(gcodePath, "r", encoding="utf-8", errors="replace") as gcodeFile:
            for line in gcodeFile:
                odometer.processGCodeLine(line.strip())

        filament = {}
        for toolIndex, length in enumerate(odometer.getExtrusionAmount()):
            if length > 0.0:
                # a tool the file does not use stays absent - allowedToPrint() reads the
                # tools in use from the keys
                filament["tool%d" % toolIndex] = {"length": length}
        return filament

    def _analyseAndStore(self, path, entry):
        try:
            filament = self.analyse(entry["copyPath"])
        except Exception:
            self._logger.exception("Could not analyse the upload 'printer:%s'" % path)
            return
        finally:
            self._removeCopy(entry)

        with self._lock:
            store = self._loadStore()
            if filament:
                store[path.lower()] = {
                    "path": path,
                    "uploadedAs": entry["path"],
                    "analysedAt": datetime.now().isoformat(),
                    "filament": filament,
                }
            else:
                # an upload without any extrusion replaces whatever was known for the name
                store.pop(path.lower(), None)
            self._saveStore(store)

        if filament:
            self._logger.info(
                "filament usage of 'printer:%s' analysed on upload: %s"
                % (
                    path,
                    ", ".join(
                        "%s %.1fmm" % (tool, data["length"])
                        for tool, data in sorted(filament.items())
                    ),
                )
            )
        else:
            self._logger.info(
                "the upload 'printer:%s' extrudes nothing, no filament usage kept"
                % path
            )

    ############################################################################ internals

    def _startThread(self, target, *args):
        threading.Thread(
            target=target,
            args=args,
            name="SpoolManager-PrinterUploadAnalysis",
            daemon=True,
        ).start()

    def _forgetPending(self, key, entry):
        with self._lock:
            if self._pending.get(key) is entry:
                del self._pending[key]
        self._removeCopy(entry)

    def _popNewestCompletePending(self):
        # caller holds the lock
        newestKey = None
        for key, entry in self._pending.items():
            if not entry["complete"]:
                continue
            if (
                newestKey is None
                or entry["startedAt"] > self._pending[newestKey]["startedAt"]
            ):
                newestKey = key
        return self._pending.pop(newestKey) if newestKey is not None else None

    def _dropStalePending(self):
        threshold = time.time() - self.PENDING_TIMEOUT_SECONDS
        with self._lock:
            staleKeys = [
                key
                for key, entry in self._pending.items()
                if entry["startedAt"] < threshold
            ]
            staleEntries = [self._pending.pop(key) for key in staleKeys]
        for entry in staleEntries:
            self._removeCopy(entry)

    def _removeCopy(self, entry):
        try:
            os.remove(entry["copyPath"])
        except FileNotFoundError:
            pass
        except Exception as e:
            self._logger.warning(
                "Could not remove the upload copy '%s': %s" % (entry["copyPath"], e)
            )

    def _storePath(self):
        return os.path.join(self._dataFolder, self.STORE_FILE_NAME)

    def _loadStore(self):
        # caller holds the lock
        if self._store is None:
            self._store = {}
            try:
                with open(self._storePath(), "r", encoding="utf-8") as storeFile:
                    loaded = json.load(storeFile)
                if isinstance(loaded, dict):
                    self._store = loaded
            except FileNotFoundError:
                pass
            except Exception as e:
                self._logger.warning(
                    "Could not read '%s', starting empty: %s" % (self._storePath(), e)
                )
        return self._store

    def _saveStore(self, store):
        # caller holds the lock
        if len(store) > self.MAX_STORED_FILES:
            oldestFirst = sorted(
                store.items(), key=lambda item: item[1].get("analysedAt") or ""
            )
            for key, _ in oldestFirst[: len(store) - self.MAX_STORED_FILES]:
                del store[key]
        temporaryPath = self._storePath() + ".tmp"
        try:
            with open(temporaryPath, "w", encoding="utf-8") as storeFile:
                json.dump(store, storeFile, indent=1)
            os.replace(temporaryPath, self._storePath())
        except Exception as e:
            self._logger.warning("Could not write '%s': %s" % (self._storePath(), e))
