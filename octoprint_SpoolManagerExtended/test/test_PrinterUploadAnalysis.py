# coding=utf-8

# Tests for remembering the filament usage of files uploaded to a printer's own storage.
#
# A file on a serial printer's SD card cannot be read back: the serial connector has no
# download for it, and OctoPrint keeps "analysis": None in its metadata. Without anything
# else, allowedToPrint could not tell what a job from the SD card needs, and the end of a
# successful job had no sliced usage to book. The upload passes through OctoPrint though -
# PrinterUploadAnalysis takes a copy on the way in and keeps the odometer's count per file.
#
# Run with:  .venv/bin/python -m pytest octoprint_SpoolManagerExtended/test/test_PrinterUploadAnalysis.py -v

import io
import logging
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from octoprint.events import Events
from octoprint.filemanager.storage.common import StorageCapabilities
from octoprint.filemanager.util import DiskFileWrapper, StreamWrapper

from octoprint_SpoolManagerExtended import SpoolmanagerPlugin
from octoprint_SpoolManagerExtended.PrinterUploadAnalysis import (
    PrinterUploadAnalysis,
    TeeStream,
)

# absolute and relative extrusion, G92 resets, retractions and a second tool:
# tool 0 extrudes 12.5mm in total, tool 1 4.25mm
SAMPLE_GCODE = b"""; header comment
M82 ; absolute extrusion
G92 E0
G1 X10 Y10 E5.0 ; prime
G1 E3.0 ; retract
G1 X20 E8.0
G92 E0
G1 X30 E2.5
T1
G92 E0
G1 X40 E4.25
T0
M83
G1 X50 E1.5
G1 E-0.8
G1 E0.8
G1 X60 E0.5
"""


def _runNow(target, *args):
    target(*args)


################################################################################################ TeeStream


class TestTeeStream(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder)

    def test_contentPassesThroughAndIsCopied(self):
        # large enough to take several reads
        content = b"G1 X1.0 Y2.0 E0.12345\n" * 20000
        copyPath = os.path.join(self.folder, "copy")
        targetPath = os.path.join(self.folder, "target")
        ends = []

        tee = TeeStream(io.BytesIO(content), open(copyPath, "wb"), ends.append)
        StreamWrapper("job.gcode", tee).save(targetPath)

        with open(targetPath, "rb") as target:
            self.assertEqual(target.read(), content)
        with open(copyPath, "rb") as copied:
            self.assertEqual(copied.read(), content)
        self.assertEqual(ends, [True])

    def test_closedBeforeTheEndReportsIncomplete(self):
        ends = []
        tee = TeeStream(
            io.BytesIO(b"G1 E1\n" * 1000),
            open(os.path.join(self.folder, "copy"), "wb"),
            ends.append,
        )

        tee.read(10)
        tee.close()

        self.assertEqual(ends, [False])


################################################################################################ manager


class TestPrinterUploadAnalysis(unittest.TestCase):
    def setUp(self):
        self.dataFolder = tempfile.mkdtemp()
        self.analysis = self._analysis()

    def tearDown(self):
        shutil.rmtree(self.dataFolder)

    def _analysis(self):
        return PrinterUploadAnalysis(
            logging.getLogger("test.printeruploadanalysis"),
            self.dataFolder,
            runInBackground=_runNow,
        )

    def _upload(self, path, content=SAMPLE_GCODE):
        # what a preprocessor chain hands on, then what the storage does with it
        wrapped = self.analysis.wrapUpload(
            path, StreamWrapper(path, io.BytesIO(content))
        )
        wrapped.save(os.path.join(self.dataFolder, "stored-by-the-storage"))

    def _assertSampleUsage(self, filament):
        self.assertEqual(sorted(filament.keys()), ["tool0", "tool1"])
        self.assertAlmostEqual(filament["tool0"]["length"], 12.5)
        self.assertAlmostEqual(filament["tool1"]["length"], 4.25)

    def _copiesLeft(self):
        return [
            name
            for name in os.listdir(tempfile.gettempdir())
            if name.startswith("spoolmanager-upload-")
        ]

    def test_uploadToPrinterStorageIsAnalysed(self):
        copiesBefore = set(self._copiesLeft())
        self._upload("rocket~1.gco")

        self.analysis.onFileAdded("printer", "rocket~1.gco")

        # the printer may list the file in upper case
        self._assertSampleUsage(self.analysis.filamentFor("ROCKET~1.GCO"))
        self.assertEqual(set(self._copiesLeft()), copiesBefore)

    def test_theAnalysisIsPendingFromTheUploadUntilItsResultIsStored(self):
        held = []
        self.analysis = PrinterUploadAnalysis(
            logging.getLogger("test.printeruploadanalysis"),
            self.dataFolder,
            runInBackground=lambda target, *args: held.append((target, args)),
        )
        self.assertFalse(self.analysis.isAnalysisPending("rocket~1.gco"))

        # the copy is taken before the transfer starts, FILE_ADDED comes after it
        self._upload("rocket.gcode")
        # ...and the printer lists the file under its own name
        self.assertTrue(self.analysis.isAnalysisPending("ROCKET~1.GCO"))

        self.analysis.onFileAdded("printer", "rocket~1.gco")
        self.assertTrue(self.analysis.isAnalysisPending("ROCKET~1.GCO"))
        self.assertIsNone(self.analysis.filamentFor("ROCKET~1.GCO"))

        target, args = held.pop()
        target(*args)
        self.assertFalse(self.analysis.isAnalysisPending("ROCKET~1.GCO"))
        self._assertSampleUsage(self.analysis.filamentFor("ROCKET~1.GCO"))

    def test_aCopyForLocalStorageIsPendingOnlyUntilItsFileAdded(self):
        self._upload("Rocket.gcode")
        self.assertTrue(self.analysis.isAnalysisPending("other~1.gco"))

        self.analysis.onFileAdded("local", "Rocket.gcode")

        self.assertFalse(self.analysis.isAnalysisPending("other~1.gco"))

    def test_aCopyWhoseFileAddedNeverCameIsNotPendingForever(self):
        self._upload("rocket.gcode")
        self.analysis._pending["rocket.gcode"]["startedAt"] -= (
            PrinterUploadAnalysis.PENDING_TIMEOUT_SECONDS + 1
        )

        self.assertFalse(self.analysis.isAnalysisPending("rocket~1.gco"))
        self.assertEqual({}, self.analysis._pending)

    def test_anAnalysisThatFailsIsNotPendingForever(self):
        self._upload("rocket~1.gco")
        with mock.patch.object(self.analysis, "analyse", side_effect=ValueError()):
            self.analysis.onFileAdded("printer", "rocket~1.gco")
        self.assertFalse(self.analysis.isAnalysisPending("rocket~1.gco"))
        self.assertIsNone(self.analysis.filamentFor("rocket~1.gco"))

    def test_fileKeptUnderAnotherNameIsFound(self):
        self._upload("rocket~1.gco")

        self.analysis.onFileAdded("printer", "rocket~2.gco")

        self._assertSampleUsage(self.analysis.filamentFor("rocket~2.gco"))
        self.assertIsNone(self.analysis.filamentFor("rocket~1.gco"))

    def test_uploadToLocalStorageIsDropped(self):
        copiesBefore = set(self._copiesLeft())
        self._upload("Rocket.gcode")

        self.analysis.onFileAdded("local", "Rocket.gcode")

        self.assertIsNone(self.analysis.filamentFor("Rocket.gcode"))
        self.assertEqual(set(self._copiesLeft()), copiesBefore)
        # and a later printer upload does not pick up the dropped copy
        self.analysis.onFileAdded("printer", "other~1.gco")
        self.assertIsNone(self.analysis.filamentFor("other~1.gco"))

    def test_fileOnDiskIsCopiedAndHandedOnUnchanged(self):
        uploadPath = os.path.join(self.dataFolder, "upload.tmp")
        with open(uploadPath, "wb") as upload:
            upload.write(SAMPLE_GCODE)
        fileObject = DiskFileWrapper("rocket~1.gco", uploadPath)

        handedOn = self.analysis.wrapUpload("rocket~1.gco", fileObject)
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self.assertIs(handedOn, fileObject)
        self._assertSampleUsage(self.analysis.filamentFor("rocket~1.gco"))

    def test_abortedUploadIsNotAnalysed(self):
        wrapped = self.analysis.wrapUpload(
            "rocket~1.gco", StreamWrapper("rocket~1.gco", io.BytesIO(SAMPLE_GCODE))
        )
        stream = wrapped.stream()
        stream.read(20)
        stream.close()

        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self.assertIsNone(self.analysis.filamentFor("rocket~1.gco"))

    def test_removedFileIsForgotten(self):
        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self.analysis.onFileRemoved("printer", "ROCKET~1.GCO")

        self.assertIsNone(self.analysis.filamentFor("rocket~1.gco"))

    def test_resultSurvivesARestart(self):
        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self._assertSampleUsage(self._analysis().filamentFor("rocket~1.gco"))

    def test_newUploadWithoutExtrusionReplacesTheOldResult(self):
        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self._upload("rocket~1.gco", b"G28\nG1 X10 Y10\n")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self.assertIsNone(self.analysis.filamentFor("rocket~1.gco"))

    def test_failedTransferDropsTheResult(self):
        # OctoPrint fires FILE_ADDED when the transfer to the printer starts, so the
        # result is stored before anyone knows whether the file arrives
        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")
        self._assertSampleUsage(self.analysis.filamentFor("rocket~1.gco"))

        self.analysis.onTransferFailed("ROCKET~1.GCO")

        self.assertIsNone(self.analysis.filamentFor("rocket~1.gco"))
        self.assertIsNone(self._analysis().filamentFor("rocket~1.gco"))

    def test_transferFailingWhileTheCopyIsAnalysedKeepsNothing(self):
        queued = []
        self.analysis._runInBackground = lambda target, *args: queued.append(
            (target, args)
        )
        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self.analysis.onTransferFailed("rocket~1.gco")
        for target, args in queued:
            target(*args)

        self.assertIsNone(self.analysis.filamentFor("rocket~1.gco"))

    def test_anEarlierFailedTransferDoesNotBlockTheNextUpload(self):
        with mock.patch.object(time, "time", return_value=time.time() - 5):
            self.analysis.onTransferFailed("rocket~1.gco")

        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self._assertSampleUsage(self.analysis.filamentFor("rocket~1.gco"))

    def test_staleFailureMarkersAreForgotten(self):
        longAgo = time.time() - 2 * PrinterUploadAnalysis.PENDING_TIMEOUT_SECONDS
        with mock.patch.object(time, "time", return_value=longAgo):
            self.analysis.onTransferFailed("old~1.gco")

        self.analysis._dropStalePending()

        self.assertEqual({}, self.analysis._failedTransfers)

    def test_failedTransferWithoutAPathIsIgnored(self):
        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self.analysis.onTransferFailed(None)

        self._assertSampleUsage(self.analysis.filamentFor("rocket~1.gco"))

    def test_resultIsACopy(self):
        self._upload("rocket~1.gco")
        self.analysis.onFileAdded("printer", "rocket~1.gco")

        self.analysis.filamentFor("rocket~1.gco")["tool0"]["length"] = 0.0

        self._assertSampleUsage(self.analysis.filamentFor("rocket~1.gco"))


################################################################################################ plugin wiring


class FakeConnection(object):
    def __init__(self, **capabilities):
        self._capabilities = StorageCapabilities(**capabilities)

    def current_storage_capabilities(self):
        return self._capabilities

    # every connector has these through OctoPrint's PrinterFilesMixin - the download
    # just returns None where nothing can be downloaded
    def upload_printer_file(self, *args, **kwargs):
        pass

    def download_printer_file(self, *args, **kwargs):
        return None


def FakeSerialConnection(uploadToSdEnabled=True):
    # what the serial connector declares: files go onto the SD card, none come back
    return FakeConnection(write_file=uploadToSdEnabled, remove_file=True, metadata=True)


def FakeDownloadingConnection():
    # e.g. the bambu connector
    return FakeConnection(write_file=True, read_file=True, remove_file=True)


class FakePrinter(object):
    def __init__(self, connection):
        self._connection = connection


class RecordingAnalysis(object):
    def __init__(self):
        self.wrapped = []

    def wrapUpload(self, path, fileObject):
        self.wrapped.append(path)
        return "wrapped"


class FakePlugin(object):
    on_filePreprocessor = SpoolmanagerPlugin.on_filePreprocessor
    _printerStorageCannotBeReadBack = SpoolmanagerPlugin._printerStorageCannotBeReadBack

    def __init__(self, connection):
        self._printer = FakePrinter(connection)
        self._logger = logging.getLogger("test.printeruploadpreprocessor")
        self._printerUploadAnalysis = RecordingAnalysis()


def _isGcode(name, type=None):
    # stands in for OctoPrint's extension tree, which needs a running plugin manager
    return name.lower().endswith((".gcode", ".gco", ".g"))


@mock.patch("octoprint.filemanager.valid_file_type", side_effect=_isGcode)
class TestUploadPreprocessor(unittest.TestCase):
    def test_uploadIsCopiedForASerialPrinter(self, _):
        plugin = FakePlugin(FakeSerialConnection())

        handedOn = plugin.on_filePreprocessor("rocket~1.gco", "upload")

        self.assertEqual(handedOn, "wrapped")
        self.assertEqual(plugin._printerUploadAnalysis.wrapped, ["rocket~1.gco"])

    def test_printerThatHandsFilesBackIsLeftAlone(self, _):
        plugin = FakePlugin(FakeDownloadingConnection())

        self.assertEqual(plugin.on_filePreprocessor("job.gcode", "upload"), "upload")
        self.assertEqual(plugin._printerUploadAnalysis.wrapped, [])

    def test_nothingIsCopiedWhileUploadsToTheSdCardAreOff(self, _):
        plugin = FakePlugin(FakeSerialConnection(uploadToSdEnabled=False))

        self.assertEqual(plugin.on_filePreprocessor("job.gcode", "upload"), "upload")
        self.assertEqual(plugin._printerUploadAnalysis.wrapped, [])

    def test_nothingIsCopiedWithoutAConnection(self, _):
        plugin = FakePlugin(None)

        self.assertEqual(plugin.on_filePreprocessor("job.gcode", "upload"), "upload")
        self.assertEqual(plugin._printerUploadAnalysis.wrapped, [])

    def test_otherFileTypesAreLeftAlone(self, _):
        plugin = FakePlugin(FakeSerialConnection())

        self.assertEqual(plugin.on_filePreprocessor("model.stl", "upload"), "upload")
        self.assertEqual(plugin._printerUploadAnalysis.wrapped, [])


class RecordingConnection(FakeConnection):
    def __init__(self, **capabilities):
        FakeConnection.__init__(self, **capabilities)
        self.downloads = []

    def get_printer_files(self, *args, **kwargs):
        return []

    def get_printer_file(self, *args, **kwargs):
        return None

    def download_printer_file(self, path, *args, **kwargs):
        self.downloads.append(path)
        raise RuntimeError("printer offline")


class FakeDownloadPlugin(object):
    _getFilamentFromPrinterFile = SpoolmanagerPlugin._getFilamentFromPrinterFile
    _printerFileLock = SpoolmanagerPlugin._printerFileLock
    _readPrinterFileFilament = SpoolmanagerPlugin._readPrinterFileFilament
    _resolvePrinterFilePath = SpoolmanagerPlugin._resolvePrinterFilePath
    _parseFilamentLengthsFromGcodeComments = (
        SpoolmanagerPlugin._parseFilamentLengthsFromGcodeComments
    )
    _parseFilamentCommentValues = SpoolmanagerPlugin._parseFilamentCommentValues
    _streamLength = SpoolmanagerPlugin._streamLength
    FILAMENT_USED_MM_PATTERN = SpoolmanagerPlugin.FILAMENT_USED_MM_PATTERN
    FILAMENT_USED_CM3_PATTERN = SpoolmanagerPlugin.FILAMENT_USED_CM3_PATTERN

    def __init__(self, connection):
        self._printer = FakePrinter(connection)
        self._logger = logging.getLogger("test.printerfiledownload")
        self._printerFileFilamentCache = {}
        self._printerFileLocks = {}
        self._printerFileLocksGuard = threading.Lock()
        self._unslicedJobFile = None
        self.clientMessages = []

    def _sendDataToClient(self, payloadDict):
        self.clientMessages.append(payloadDict["action"])


class TestPrinterFileDownload(unittest.TestCase):
    def test_storageThatCannotBeReadIsNotAskedForTheFile(self):
        # the browser used to get "analysing the printer's file" for an SD card's file
        connection = RecordingConnection(write_file=True)
        plugin = FakeDownloadPlugin(connection)

        self.assertIsNone(plugin._getFilamentFromPrinterFile("rocket~1.gco", 1))
        self.assertEqual(connection.downloads, [])
        self.assertEqual(plugin.clientMessages, [])

    def test_storageThatCanBeReadIsStillAsked(self):
        connection = RecordingConnection(write_file=True, read_file=True)
        plugin = FakeDownloadPlugin(connection)

        plugin._getFilamentFromPrinterFile("job.gcode", 1)

        self.assertEqual(connection.downloads, ["job.gcode"])
        self.assertIn("printerFileAnalysisStarted", plugin.clientMessages)


class FakePrinterFile(object):
    size = 1234
    date = 1700000000


class BlockingDownloadConnection(FakeConnection):
    """A printer whose download of a file takes until the test lets it finish."""

    def __init__(self):
        FakeConnection.__init__(self, write_file=True, read_file=True)
        self.downloads = []
        self.firstDownloadStarted = threading.Event()
        self.secondDownloadStarted = threading.Event()
        self.finishDownloads = threading.Event()

    def get_printer_files(self, *args, **kwargs):
        return []

    def get_printer_file(self, *args, **kwargs):
        return FakePrinterFile()

    def download_printer_file(self, path, *args, **kwargs):
        self.downloads.append(path)
        if len(self.downloads) == 1:
            self.firstDownloadStarted.set()
        else:
            self.secondDownloadStarted.set()
        self.finishDownloads.wait(5)
        return io.BytesIO(
            b"G1 X1 E1\n; filament used [mm] = 0.00, 0.00, 0.00, 297.65\n"
        )


class TestConcurrentPrinterFileReads(unittest.TestCase):
    def test_secondCallerWaitsForTheFirstDownload(self):
        # PrintJobHistoryExtended asks for a job's usage from its own thread while this
        # plugin's PRINT_STARTED handling reads the same file
        connection = BlockingDownloadConnection()
        plugin = FakeDownloadPlugin(connection)
        results = []

        def read():
            results.append(plugin._getFilamentFromPrinterFile("job.gcode", 1))

        first = threading.Thread(target=read)
        first.start()
        self.assertTrue(connection.firstDownloadStarted.wait(5))
        second = threading.Thread(target=read)
        second.start()
        # give the second caller the time it needs to start a download of its own
        connection.secondDownloadStarted.wait(0.5)
        connection.finishDownloads.set()
        first.join(5)
        second.join(5)

        self.assertEqual(connection.downloads, ["job.gcode"])
        self.assertEqual(results, [{"tool3": {"length": 297.65}}] * 2)


class TestTransferFailedEvent(unittest.TestCase):
    def test_theFilesPathOnThePrinterReachesTheAnalysis(self):
        recorded = []
        plugin = types.SimpleNamespace(
            _printerUploadAnalysis=types.SimpleNamespace(
                onTransferFailed=recorded.append
            )
        )

        SpoolmanagerPlugin.on_event(
            plugin,
            Events.TRANSFER_FAILED,
            {"local": "rocket.gcode", "remote": "ROCKET~1.GCO", "time": 3.2},
        )

        self.assertEqual(["ROCKET~1.GCO"], recorded)


class MetaDataReadingPlugin(object):
    """_readingFilamentMetaData() on a job whose metadata lookup comes back empty."""

    _readingFilamentMetaData = SpoolmanagerPlugin._readingFilamentMetaData
    _uploadAnalysisStillRunning = SpoolmanagerPlugin._uploadAnalysisStillRunning

    def __init__(self, origin, pending):
        # one answer per isAnalysisPending() call, or the same for all
        answers = list(pending) if isinstance(pending, list) else [pending, pending]
        self._logger = logging.getLogger("test.readingfilamentmetadata")
        self._printer = types.SimpleNamespace(current_job=None)
        self._printerUploadAnalysis = types.SimpleNamespace(
            isAnalysisPending=lambda path: answers.pop(0)
        )
        self._origin = origin
        self.metaDataFilamentLengths = None
        self._unslicedJobFile = None

    def _getCurrentJobFileLocation(self):
        return self._origin, "ROCKET~1.GCO"

    def _getFilamentMetaData(self, origin, path, plate=1):
        return None


class TestMetaDataMissingWhileTheUploadIsAnalysed(unittest.TestCase):
    # Seen on every SD transfer: OctoPrint selects the file as the job when the transfer
    # starts, before FILE_ADDED, and the selection checks warned four times about missing
    # metadata that the analysis delivered 0.2 s later.

    def _levels(self, plugin):
        with self.assertLogs(plugin._logger, level="DEBUG") as logged:
            self.assertFalse(plugin._readingFilamentMetaData())
        self.assertEqual([], plugin.metaDataFilamentLengths)
        return [record.levelname for record in logged.records]

    def test_aRunningAnalysisIsNoWarning(self):
        self.assertEqual(
            ["DEBUG"], self._levels(MetaDataReadingPlugin("printer", True))
        )

    def test_anAnalysisLandingDuringTheLookupIsNoWarningEither(self):
        # seen live: the lookup started before the result was stored, and by the time
        # the lookup came back empty the analysis was no longer pending
        self.assertEqual(
            ["DEBUG"], self._levels(MetaDataReadingPlugin("printer", [True, False]))
        )

    def test_aFileWithoutAnyAnalysisStillWarns(self):
        self.assertEqual(
            ["WARNING"], self._levels(MetaDataReadingPlugin("printer", False))
        )

    def test_aLocalFileIsNotMistakenForAnUpload(self):
        # the analysis store is keyed by the name on the printer; a local job of the same
        # name has its own metadata and gets the warning when that is missing
        self.assertEqual(
            ["WARNING"], self._levels(MetaDataReadingPlugin("local", True))
        )


if __name__ == "__main__":
    unittest.main()
