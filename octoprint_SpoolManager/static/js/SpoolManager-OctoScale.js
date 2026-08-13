/**
 * Shared OctoScale helpers, used by both the Add Spool Wizard and the classic edit dialog.
 *
 * Two independent pieces of state, deliberately kept apart because the two panels are opened
 * at different times and each has to stop its own polling:
 *   SpoolManagerOctoScaleWeighing - live weight readout + tare + "use this value"
 *   SpoolManagerOctoScaleTagWriter - NFC tag detection, overwrite warning, write + verify
 *
 * Both poll through the plugin backend (see the /octoscale/* routes): the device serves plain
 * HTTP without CORS headers, so the browser cannot reach it directly.
 *
 * Polling only runs while a panel is actually visible. Every start() is paired with a stop(),
 * and stop() is also called from the dialogs' hide handlers - a forgotten interval would keep
 * hitting the device (and the log) for as long as the browser tab stays open.
 */

const OCTOSCALE_WEIGHT_POLL_INTERVAL_MS = 1000;
const OCTOSCALE_NFC_POLL_INTERVAL_MS = 1200;
const OCTOSCALE_WRITE_STATUS_POLL_INTERVAL_MS = 500;
// The firmware self-clears its write status once read as done - if that never arrives
// (device rebooted mid-write, lost the flag, etc.) stop polling rather than spin forever.
const OCTOSCALE_WRITE_STATUS_TIMEOUT_MS = 20000;

// The device is a single-core ESP32-S2 whose NFC task competes with its web server, so individual
// requests occasionally take seconds or drop out entirely while the reader is polling. Reporting
// the first failure would make the readout flicker between a value and "no connection" although
// weighing and writing work fine, so a couple of consecutive failures are tolerated before an
// error is shown - and the last known value stays on screen meanwhile.
const OCTOSCALE_TOLERATED_CONSECUTIVE_FAILURES = 3;

function SpoolManagerOctoScaleWeighing(apiClient, pluginSettings) {
    var self = this;

    self.apiClient = apiClient;
    // only used to pick the unit of the readout label; currentWeight() stays in grams
    self.pluginSettings = pluginSettings;

    self.isActive = ko.observable(false);
    self.currentWeight = ko.observable(null);
    self.errorMessage = ko.observable(null);
    self.isTaring = ko.observable(false);

    var pollTimerId = null;

    self.hasReading = ko.pureComputed(function () {
        return self.currentWeight() != null;
    });

    self.currentWeightText = ko.pureComputed(function () {
        var weight = self.currentWeight();
        if (weight == null) {
            return "--";
        }
        // the device always reports grams (responseData.grams); only the label converts
        return SPOOLMANAGER_UTILS.formatWeightForDisplay(
            Math.round(weight * 10) / 10,
            self.pluginSettings
        );
    });

    var consecutiveFailures = 0;

    var readWeight = function () {
        self.apiClient.getOctoScaleWeight(function (responseData) {
            // a late answer arriving after the panel was closed must not revive the display
            if (self.isActive() == false) {
                return;
            }
            if (responseData && responseData.success === true) {
                consecutiveFailures = 0;
                self.currentWeight(responseData.grams);
                self.errorMessage(null);
                return;
            }

            consecutiveFailures++;
            if (consecutiveFailures < OCTOSCALE_TOLERATED_CONSECUTIVE_FAILURES) {
                // single hiccup: keep showing the last reading instead of flickering
                return;
            }
            self.currentWeight(null);
            self.errorMessage(
                responseData && responseData.error
                    ? responseData.error
                    : "Could not read the weight."
            );
        });
    };

    self.start = function () {
        if (pollTimerId != null) {
            return;
        }
        self.isActive(true);
        self.errorMessage(null);
        consecutiveFailures = 0;
        readWeight();
        pollTimerId = setInterval(readWeight, OCTOSCALE_WEIGHT_POLL_INTERVAL_MS);
    };

    self.stop = function () {
        if (pollTimerId != null) {
            clearInterval(pollTimerId);
            pollTimerId = null;
        }
        self.isActive(false);
        self.currentWeight(null);
        self.errorMessage(null);
    };

    self.toggle = function () {
        if (self.isActive()) {
            self.stop();
        } else {
            self.start();
        }
    };

    self.tare = function () {
        self.isTaring(true);
        self.apiClient.tareOctoScale(function (responseData) {
            self.isTaring(false);
            if (!responseData || responseData.success !== true) {
                self.errorMessage(
                    responseData && responseData.error
                        ? responseData.error
                        : "Could not tare the scale."
                );
            } else {
                self.errorMessage(null);
            }
        });
    };
}

function SpoolManagerOctoScaleTagWriter(apiClient) {
    var self = this;

    self.apiClient = apiClient;

    self.isActive = ko.observable(false);
    self.tagPresent = ko.observable(false);
    self.tagSpoolId = ko.observable(null); // spool id already stored on the tag, if any
    self.tagSpoolDisplayName = ko.observable(null);
    self.errorMessage = ko.observable(null);
    self.isWriting = ko.observable(false);
    self.writeSucceeded = ko.observable(false);
    self.overwriteConfirmed = ko.observable(false);

    // Tag type as detected by the firmware: exactly one of "mifareClassic1k", "ntag",
    // "nfcv", "unknown" (verified against the firmware source - NTAG213/215/216 are NOT
    // distinguished in this field, that detail lives in formatLabel/capacityBytes
    // instead). writeFormat/formatLabel are the format the firmware will actually pick
    // (see common/TagFormats.py's formatForTagType for the same mapping, used server-side
    // only as a fallback until the device's own answer arrives).
    self.tagType = ko.observable(null);
    self.tagTypeName = ko.observable(null); // coarse protocol class, e.g. "NFC-A"/"NFC-V"
    self.writeFormat = ko.observable(null);
    self.formatLabel = ko.observable(null); // human label, e.g. "Mifare Classic 1K"
    self.hasExtendedData = ko.observable(false); // tag already carries an extended payload

    // Result of the last completed write, once the async write finishes.
    self.writeFormatUsed = ko.observable(null);
    self.writeBytesWritten = ko.observable(null);
    self.writeDroppedFields = ko.observableArray([]);

    // The spool the tag should point to. Set by the caller before starting.
    self.targetDatabaseId = ko.observable(null);

    var pollTimerId = null;

    // A tag that already carries a *different* spool id would be silently re-labelled by a
    // write, so the user has to confirm first.
    self.needsOverwriteConfirmation = ko.pureComputed(function () {
        var existingId = self.tagSpoolId();
        if (existingId == null) {
            return false;
        }
        return existingId != self.targetDatabaseId();
    });

    self.overwriteWarningText = ko.pureComputed(function () {
        var existingId = self.tagSpoolId();
        if (existingId == null) {
            return "";
        }
        var displayName = self.tagSpoolDisplayName();
        if (displayName) {
            return (
                "This tag already belongs to '" +
                displayName +
                "' (id " +
                existingId +
                ")."
            );
        }
        return (
            "This tag already carries spool id " +
            existingId +
            ", which is not in this database."
        );
    });

    self.canWrite = ko.pureComputed(function () {
        if (self.isWriting() || self.targetDatabaseId() == null) {
            return false;
        }
        if (self.tagPresent() != true) {
            return false;
        }
        if (self.needsOverwriteConfirmation() && self.overwriteConfirmed() != true) {
            return false;
        }
        return true;
    });

    var consecutiveFailures = 0;

    var readNfcStatus = function () {
        self.apiClient.getOctoScaleNfcStatus(function (responseData) {
            if (self.isActive() == false) {
                return;
            }
            if (responseData && responseData.success === true) {
                consecutiveFailures = 0;
                var previousSpoolId = self.tagSpoolId();
                self.tagPresent(responseData.present === true);
                self.tagSpoolId(
                    responseData.present === true ? responseData.spoolId : null
                );
                self.tagSpoolDisplayName(
                    responseData.present === true ? responseData.spoolDisplayName : null
                );
                self.tagType(responseData.present === true ? responseData.tagType : null);
                self.tagTypeName(
                    responseData.present === true ? responseData.tagTypeName : null
                );
                self.writeFormat(
                    responseData.present === true ? responseData.writeFormat : null
                );
                self.formatLabel(
                    responseData.present === true ? responseData.formatLabel : null
                );
                self.hasExtendedData(
                    responseData.present === true &&
                        responseData.hasExtendedData === true
                );
                // a different tag on the reader invalidates a confirmation given for the previous one
                if (previousSpoolId != self.tagSpoolId()) {
                    self.overwriteConfirmed(false);
                }
                self.errorMessage(null);
                return;
            }

            consecutiveFailures++;
            if (consecutiveFailures < OCTOSCALE_TOLERATED_CONSECUTIVE_FAILURES) {
                // hold the current tag state through a hiccup rather than claiming the tag vanished
                return;
            }
            self.tagPresent(false);
            self.errorMessage(
                responseData && responseData.error
                    ? responseData.error
                    : "Could not read the NFC status."
            );
        });
    };

    self.start = function (databaseId) {
        self.targetDatabaseId(databaseId);
        self.writeSucceeded(false);
        self.overwriteConfirmed(false);
        self.errorMessage(null);
        if (pollTimerId != null) {
            return;
        }
        self.isActive(true);
        consecutiveFailures = 0;
        readNfcStatus();
        pollTimerId = setInterval(readNfcStatus, OCTOSCALE_NFC_POLL_INTERVAL_MS);
    };

    var writeStatusTimerId = null;
    var writeStatusElapsedMs = 0;

    var stopWriteStatusPolling = function () {
        if (writeStatusTimerId != null) {
            clearInterval(writeStatusTimerId);
            writeStatusTimerId = null;
        }
        writeStatusElapsedMs = 0;
    };

    self.stop = function () {
        if (pollTimerId != null) {
            clearInterval(pollTimerId);
            pollTimerId = null;
        }
        stopWriteStatusPolling();
        self.isActive(false);
        self.isWriting(false);
        self.tagPresent(false);
        self.tagSpoolId(null);
        self.tagSpoolDisplayName(null);
        self.tagType(null);
        self.tagTypeName(null);
        self.writeFormat(null);
        self.formatLabel(null);
        self.hasExtendedData(false);
        self.overwriteConfirmed(false);
        self.errorMessage(null);
    };

    self.confirmOverwrite = function () {
        self.overwriteConfirmed(true);
    };

    // Writes are async on the firmware: writeOctoScaleTag only starts the write (device
    // answers 202), the actual result is polled via getOctoScaleWriteStatus until "done".
    self.writeTag = function () {
        if (self.canWrite() == false) {
            return;
        }
        self.isWriting(true);
        self.errorMessage(null);
        self.writeSucceeded(false);
        self.writeFormatUsed(null);
        self.writeBytesWritten(null);
        self.writeDroppedFields([]);

        self.apiClient.writeOctoScaleTag(self.targetDatabaseId(), function (responseData) {
            if (self.isActive() == false) {
                return;
            }
            if (!responseData || responseData.success !== true) {
                self.isWriting(false);
                self.writeSucceeded(false);
                self.errorMessage(
                    responseData && responseData.error
                        ? responseData.error
                        : "Could not write the tag."
                );
                return;
            }

            // Accepted (202) - now poll /nfcwritestatus until the firmware reports done.
            writeStatusElapsedMs = 0;
            writeStatusTimerId = setInterval(function () {
                if (self.isActive() == false) {
                    stopWriteStatusPolling();
                    return;
                }
                writeStatusElapsedMs += OCTOSCALE_WRITE_STATUS_POLL_INTERVAL_MS;
                if (writeStatusElapsedMs >= OCTOSCALE_WRITE_STATUS_TIMEOUT_MS) {
                    stopWriteStatusPolling();
                    self.isWriting(false);
                    self.writeSucceeded(false);
                    self.errorMessage(
                        "OctoScale did not report a write result in time."
                    );
                    return;
                }

                self.apiClient.getOctoScaleWriteStatus(function (statusData) {
                    if (self.isActive() == false) {
                        stopWriteStatusPolling();
                        return;
                    }
                    if (!statusData || statusData.success !== true) {
                        // transient read failure while polling - keep trying until the timeout
                        return;
                    }
                    if (statusData.done !== true) {
                        return;
                    }

                    stopWriteStatusPolling();
                    self.isWriting(false);
                    if (statusData.ok === true) {
                        self.writeSucceeded(true);
                        self.errorMessage(null);
                        self.writeFormatUsed(statusData.format || null);
                        self.writeBytesWritten(
                            statusData.bytesWritten != null
                                ? statusData.bytesWritten
                                : null
                        );
                        self.writeDroppedFields(statusData.droppedFields || []);
                    } else {
                        self.writeSucceeded(false);
                        self.errorMessage(statusData.error || "Could not write the tag.");
                    }
                });
            }, OCTOSCALE_WRITE_STATUS_POLL_INTERVAL_MS);
        });
    };
}
