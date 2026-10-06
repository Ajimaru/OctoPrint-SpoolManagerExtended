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
import unittest
from unittest import mock

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
    _resolvePrinterFilePath = SpoolmanagerPlugin._resolvePrinterFilePath

    def __init__(self, connection):
        self._printer = FakePrinter(connection)
        self._logger = logging.getLogger("test.printerfiledownload")
        self._printerFileFilamentCache = {}
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


if __name__ == "__main__":
    unittest.main()
