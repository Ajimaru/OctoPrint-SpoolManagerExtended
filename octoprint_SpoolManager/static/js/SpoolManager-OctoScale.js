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

// Diff field definitions shared by tagValueDiff() below: logical field -> label/unit/
// how to read the current value off a SpoolItem. Mirrors _buildFullSpoolPayload() in
// TagFormats.py field-for-field, since that's exactly what a write would put on the tag.
// Date fields (firstUse/lastUse/purchasedOn) are handled separately (see epoch-day
// conversion in tagValueDiff) since SpoolItem stores them as formatted strings, not the
// epoch-day ints the tag/firmware use.
var OCTOSCALE_TAG_DIFF_FIELDS = [
    {key: "material", label: "Material"},
    {key: "vendor", label: "Vendor"},
    {key: "color", label: "Color"},
    {key: "colorName", label: "Color name"},
    {key: "diameter", label: "Diameter", unit: "mm"},
    {key: "density", label: "Density", unit: "g/cm³"},
    {key: "totalWeight", label: "Total weight", unit: "g"},
    {key: "spoolWeight", label: "Spool weight", unit: "g"},
    {key: "usedWeight", label: "Used weight", unit: "g"},
    {key: "temperature", label: "Nozzle temperature", unit: "°C"},
    {key: "minTemperature", label: "Min nozzle temperature", unit: "°C"},
    {key: "maxTemperature", label: "Max nozzle temperature", unit: "°C"},
    {key: "bedTemperature", label: "Bed temperature", unit: "°C"},
    {key: "minBedTemperature", label: "Min bed temperature", unit: "°C"},
    {key: "maxBedTemperature", label: "Max bed temperature", unit: "°C"},
    {key: "remainingWeight", label: "Remaining weight", unit: "g"},
    {key: "totalLength", label: "Total length", unit: "mm"},
    {key: "usedLength", label: "Used length", unit: "mm"},
    {key: "code", label: "Code"},
    {key: "batchNumber", label: "Batch number"},
    {key: "purchasedFrom", label: "Purchased from"},
    {key: "finish", label: "Finish"},
    {key: "displayName", label: "Display name"},
    {key: "cost", label: "Cost"}
];

// firstUse/lastUse/purchasedOn are compared separately: the tag carries epoch-day ints,
// SpoolItem carries formatted strings (see PARSE_FORMAT_DATETIME/PARSE_FORMAT_DATE).
var OCTOSCALE_TAG_DIFF_DATE_FIELDS = [
    {
        key: "firstUse",
        label: "First use",
        format: SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATETIME
    },
    {
        key: "lastUse",
        label: "Last use",
        format: SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATETIME
    },
    {
        key: "purchasedOn",
        label: "Purchased on",
        format: SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATE
    }
];

var OCTOSCALE_EPOCH_DATE_MS = Date.UTC(1970, 0, 1);

function octoScaleEpochDaysToText(epochDays, format) {
    // -1 is the firmware's "not set" sentinel here too (see octoScaleNormalizeTagValue) -
    // caught explicitly since epoch day -1 (1969-12-31) is itself a valid moment() result
    // and would otherwise silently read as a real date.
    if (epochDays == null || epochDays === -1) {
        return null;
    }
    return moment(OCTOSCALE_EPOCH_DATE_MS + epochDays * 86400000)
        .utc()
        .format(format);
}

// The firmware's "extended" payload uses -1 (numbers) / "" (strings) as its "field not
// present on this tag" sentinel (confirmed against main.cpp/pn5180nfc.h's SpoolTagData -
// every numeric extended field is populated this way, including on weaker formats like
// OpenSpool/OpenPrintTag that don't carry every field) - normalize that to null so those
// fields read as "not set" instead of showing up as a spurious diff against -1.
function octoScaleNormalizeTagValue(rawValue) {
    if (rawValue === "") {
        return null;
    }
    if (typeof rawValue === "number" && rawValue === -1) {
        return null;
    }
    return rawValue;
}

// Loosely-typed equality: the tag sends JSON numbers/strings, SpoolItem may hold either
// depending on the field - normalize both sides before comparing so e.g. 195 vs "195" or
// 0.2 vs "0.2" don't show up as spurious diffs.
function octoScaleValuesDiffer(tagValue, currentValue) {
    var tagEmpty = tagValue == null || tagValue === "";
    var currentEmpty = currentValue == null || currentValue === "";
    if (tagEmpty && currentEmpty) {
        return false;
    }
    if (tagEmpty !== currentEmpty) {
        return true;
    }
    if (typeof tagValue === "number" || typeof currentValue === "number") {
        var tagNum = parseFloat(tagValue);
        var currentNum = parseFloat(currentValue);
        if (!isNaN(tagNum) && !isNaN(currentNum)) {
            return Math.abs(tagNum - currentNum) > 1e-9;
        }
    }
    return String(tagValue) !== String(currentValue);
}

// Plain-text rendering for a diff row's value: appends the unit if given and the value
// is numeric, falls back to "(not set)" for null/empty so the diff never shows a blank cell.
function octoScaleFormatDiffValue(value, unit) {
    if (value == null || value === "") {
        return "(not set)";
    }
    if (unit && (typeof value === "number" || !isNaN(parseFloat(value)))) {
        return value + " " + unit;
    }
    return String(value);
}

function SpoolManagerOctoScaleTagWriter(apiClient) {
    var self = this;

    self.apiClient = apiClient;

    self.isActive = ko.observable(false);
    self.tagPresent = ko.observable(false);
    self.tagSpoolId = ko.observable(null); // spool id already stored on the tag, if any
    self.tagSpoolDisplayName = ko.observable(null);
    self.tagValues = ko.observable(null); // raw "extended" payload of the tag currently on the reader, if any
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
    // Human-readable warning from the firmware (e.g. tag too small for the chosen
    // format), null when the write had no caveats.
    self.writeWarning = ko.observable(null);

    // UID of the tag currently on the reader, as last reported by /octoscale/nfc. Used as a
    // fallback source for the OpenPrintTag auto-teach-in below when /nfcwritestatus itself
    // doesn't carry a uid (older firmware) - see teachRfidTagKeyIfNeeded().
    self.tagUid = ko.observable(null);

    // Outcome of the last auto-teach-in attempt (see teachRfidTagKeyIfNeeded below); null
    // until a teach-in has actually been attempted. Never blocks/delays writeSucceeded -
    // teach-in is a convenience on top of a write that already completed.
    self.teachInResult = ko.observable(null); // {taught, reason, existingKey, newKey, conflictingSpoolId, conflictingSpoolDisplayName} or null
    self.isTeachingIn = ko.observable(false);

    // The spool the tag should point to. Set by the caller before starting.
    self.targetDatabaseId = ko.observable(null);
    // The SpoolItem being edited, for diffing tagValues() against - set by the caller
    // before starting alongside targetDatabaseId. Only used for the diff display; nothing
    // here writes back to it.
    self.targetSpoolItem = ko.observable(null);

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

    // True once a tag on the reader is confirmed to already belong to the spool being
    // edited - the update case, as opposed to a blank tag or one belonging to another spool.
    self.isOwnSpoolTag = ko.pureComputed(function () {
        var existingId = self.tagSpoolId();
        return (
            existingId != null &&
            existingId == self.targetDatabaseId() &&
            self.hasExtendedData() === true
        );
    });

    // Full before/after diff between the tag's extended payload and the spool currently
    // being edited, field-for-field against what a write would actually put on the tag
    // (see _buildFullSpoolPayload() in TagFormats.py / OCTOSCALE_TAG_DIFF_FIELDS above).
    // Only meaningful (and only shown) for isOwnSpoolTag() - an unrelated/foreign tag's
    // values aren't "changes", they're just a different tag.
    self.tagValueDiff = ko.pureComputed(function () {
        var tagValues = self.tagValues();
        var spoolItem = self.targetSpoolItem();
        if (!tagValues || !spoolItem) {
            return [];
        }

        var diffs = [];

        OCTOSCALE_TAG_DIFF_FIELDS.forEach(function (field) {
            if (!Object.prototype.hasOwnProperty.call(tagValues, field.key)) {
                return;
            }
            var tagValue = octoScaleNormalizeTagValue(tagValues[field.key]);
            var currentValue =
                typeof spoolItem[field.key] === "function" ? spoolItem[field.key]() : null;
            if (!octoScaleValuesDiffer(tagValue, currentValue)) {
                return;
            }
            diffs.push({
                label: field.label,
                oldValueText: octoScaleFormatDiffValue(tagValue, field.unit),
                newValueText: octoScaleFormatDiffValue(currentValue, field.unit)
            });
        });

        OCTOSCALE_TAG_DIFF_DATE_FIELDS.forEach(function (field) {
            if (!Object.prototype.hasOwnProperty.call(tagValues, field.key)) {
                return;
            }
            var tagEpochDays = tagValues[field.key];
            var tagValueText = octoScaleEpochDaysToText(tagEpochDays, field.format);
            var currentValueText =
                typeof spoolItem[field.key] === "function" ? spoolItem[field.key]() : null;
            if (!octoScaleValuesDiffer(tagValueText, currentValueText)) {
                return;
            }
            diffs.push({
                label: field.label,
                oldValueText: tagValueText || "(not set)",
                newValueText: currentValueText || "(not set)"
            });
        });

        return diffs;
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
                self.tagUid(responseData.present === true ? responseData.uid : null);
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
                self.tagValues(
                    responseData.present === true ? responseData.extended || null : null
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

    self.start = function (databaseId, spoolItem) {
        self.targetDatabaseId(databaseId);
        self.targetSpoolItem(spoolItem || null);
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
        self.tagUid(null);
        self.tagSpoolId(null);
        self.tagSpoolDisplayName(null);
        self.tagType(null);
        self.tagTypeName(null);
        self.writeFormat(null);
        self.formatLabel(null);
        self.hasExtendedData(false);
        self.tagValues(null);
        self.targetSpoolItem(null);
        self.overwriteConfirmed(false);
        self.errorMessage(null);
        self.teachInResult(null);
        self.isTeachingIn(false);
    };

    self.confirmOverwrite = function () {
        self.overwriteConfirmed(true);
    };

    // A written tag's format is only ever teach-in relevant if it's an OpenPrintTag format -
    // extended/OpenSpool already carry the database id on the tag, so auto-writing rfidTagKey
    // for them would be surprising. Matched loosely (substring, case-insensitive) against
    // both the setting value ("openPrintTag") and the TagFormats id ("nfcvOpenPrintTag"),
    // since the exact string the firmware echoes back in /nfcwritestatus's "format" field
    // isn't guaranteed to match either spelling precisely.
    var isOpenPrintTagFormat = function (formatString) {
        if (!formatString) {
            return false;
        }
        return formatString.toLowerCase().indexOf("openprinttag") !== -1;
    };

    // After a successful OpenPrintTag write, teaches the spool's rfidTagKey from the tag's
    // UID automatically (see POST /octoscale/teachRfidTagKey) - OpenPrintTag tags carry no
    // database id, so without this the user would have to copy the UID in by hand. Prefers
    // the UID of the tag actually written (statusData.uid, newer firmware); falls back to
    // the UID last seen on the reader via /octoscale/nfc otherwise. Never blocks or delays
    // writeSucceeded - this runs after the write is already reported done.
    self.teachRfidTagKeyIfNeeded = function (statusData) {
        self.teachInResult(null);
        if (!isOpenPrintTagFormat(statusData.format)) {
            return;
        }
        var uid = statusData.uid || self.tagUid();
        if (!uid) {
            return;
        }

        self.isTeachingIn(true);
        self.apiClient.teachOctoScaleRfidTagKey(
            self.targetDatabaseId(),
            uid,
            false,
            function (responseData) {
                self.isTeachingIn(false);
                if (self.isActive() == false) {
                    return;
                }
                if (responseData && responseData.success === true) {
                    self.teachInResult(responseData);
                } else {
                    self.teachInResult({
                        taught: false,
                        reason: "error",
                        error:
                            (responseData && responseData.error) ||
                            "Could not teach in the tag UID.",
                    });
                }
            }
        );
    };

    // Re-attempts a teach-in that was blocked by an existing/conflicting rfidTagKey, this
    // time overriding it. Only meaningful after teachInResult() reports "existingKeyDiffers"
    // or "collision" - the UI offers this as an explicit "Assign anyway" action.
    self.forceTeachRfidTagKey = function () {
        var uid = self.tagUid();
        if (!uid || self.targetDatabaseId() == null) {
            return;
        }
        self.isTeachingIn(true);
        self.apiClient.teachOctoScaleRfidTagKey(
            self.targetDatabaseId(),
            uid,
            true,
            function (responseData) {
                self.isTeachingIn(false);
                if (self.isActive() == false) {
                    return;
                }
                if (responseData && responseData.success === true) {
                    self.teachInResult(responseData);
                } else {
                    self.teachInResult({
                        taught: false,
                        reason: "error",
                        error:
                            (responseData && responseData.error) ||
                            "Could not teach in the tag UID.",
                    });
                }
            }
        );
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
        self.writeWarning(null);
        self.teachInResult(null);

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
                        self.writeWarning(statusData.warning || null);
                        self.teachRfidTagKeyIfNeeded(statusData);
                    } else {
                        self.writeSucceeded(false);
                        self.errorMessage(statusData.error || "Could not write the tag.");
                    }
                });
            }, OCTOSCALE_WRITE_STATUS_POLL_INTERVAL_MS);
        });
    };
}
