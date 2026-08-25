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

// Individual requests to the device occasionally take seconds or drop out entirely while the
// reader is polling (cause unconfirmed - the device is a dual-core ESP32-S3 with NFC and web
// server on separate cores, so it is not core contention; WiFi power-save is the likelier
// candidate). Reporting the first failure would make the readout flicker between a value and
// "no connection" although weighing and writing work fine, so a couple of consecutive failures
// are tolerated before an error is shown - and the last known value stays on screen meanwhile.
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
    // Hex colours are the same colour whatever the case: the firmware echoes "#FD7412"
    // where the spool holds "#fd7412", which would otherwise show up as a change on every
    // single write of an unmodified spool.
    {key: "color", label: "Color", caseInsensitive: true},
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
    {key: "dryingTemperature", label: "Drying temperature", unit: "°C"},
    // The tag carries minutes (OpenPrintTag spec key 58), SpoolManager stores hours - so
    // the tag value has to be divided before it can be compared with, or shown next to,
    // the spool's own value. Without this a spool set to 8 h would diff against its own
    // tag as "480 h -> 8 h".
    {key: "dryingTime", label: "Drying time", unit: "h", tagValueDivisor: 60},
    // No unit: TD is a dimensionless opacity number (0.1-100), not a length.
    {key: "td", label: "Transmission distance (TD)"},
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
        // Optional companion field carrying the time of day (see octoScaleTagDateText).
        minuteOfDayKey: "firstUseMinuteOfDay",
        label: "First use",
        format: SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATETIME
    },
    {
        key: "lastUse",
        minuteOfDayKey: "lastUseMinuteOfDay",
        label: "Last use",
        format: SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATETIME
    },
    {
        key: "purchasedOn",
        minuteOfDayKey: "purchasedOnMinuteOfDay",
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

// The day and the time of day are two separate fields on the tag: the day has always been
// there, the minute-of-day is optional and may be absent (older firmware, older tag, or a
// value that never had a time). Missing or sentinel means midnight, which is exactly the
// behaviour that existed before the field was introduced.
function octoScaleTagDateText(tagValues, field) {
    var epochDays = tagValues[field.key];
    if (epochDays == null || epochDays === -1) {
        return null;
    }

    var offsetMs = 0;
    if (field.minuteOfDayKey) {
        var minuteOfDay = tagValues[field.minuteOfDayKey];
        // 0xFFFF is the firmware's uint16 "not set" sentinel; anything outside a real day
        // is treated the same way rather than shifting the date into the next one.
        if (
            typeof minuteOfDay === "number" &&
            minuteOfDay >= 0 &&
            minuteOfDay < 1440
        ) {
            offsetMs = minuteOfDay * 60000;
        }
    }

    return moment(OCTOSCALE_EPOCH_DATE_MS + epochDays * 86400000 + offsetMs)
        .utc()
        .format(field.format);
}

// The firmware's "extended" payload uses -1 (numbers) / "" (strings) as its "field not
// present on this tag" sentinel (confirmed against main.cpp/pn5180nfc.h's SpoolTagData -
// every numeric extended field is populated this way, including on weaker formats like
// OpenSpool/OpenPrintTag that don't carry every field) - normalize that to null so those
// fields read as "not set" instead of showing up as a spurious diff against -1.
function octoScaleNormalizeTagValue(rawValue, divisor) {
    if (rawValue === "") {
        return null;
    }
    if (typeof rawValue === "number" && rawValue === -1) {
        return null;
    }
    // Some fields are stored on the tag in a different unit than SpoolManager keeps them -
    // drying time is minutes there, hours here (see OCTOSCALE_TAG_DIFF_FIELDS). Convert
    // before anything compares or displays the value, or a spool would diff against its own
    // tag.
    if (divisor && typeof divisor === "number" && divisor !== 0) {
        var numeric = parseFloat(rawValue);
        if (!isNaN(numeric)) {
            return numeric / divisor;
        }
    }
    return rawValue;
}

// Loosely-typed equality: the tag sends JSON numbers/strings, SpoolItem may hold either
// depending on the field - normalize both sides before comparing so e.g. 195 vs "195" or
// 0.2 vs "0.2" don't show up as spurious diffs.
function octoScaleValuesDiffer(tagValue, currentValue, caseInsensitive) {
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
    var tagText = String(tagValue);
    var currentText = String(currentValue);
    if (caseInsensitive) {
        // Only for fields where case carries no meaning (hex colours). Vendor and colour
        // *name* are deliberately left case-sensitive: there a changed capitalization is a
        // real edit the user should see.
        return tagText.toLowerCase() !== currentText.toLowerCase();
    }
    return tagText !== currentText;
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

function SpoolManagerOctoScaleTagWriter(apiClient, pluginSettings) {
    var self = this;

    self.apiClient = apiClient;
    // Only used to decide whether the "read tag" affordance is offered at all; the backend
    // enforces the same setting, this just avoids showing a button that would be refused.
    self.pluginSettings = pluginSettings;

    self.isActive = ko.observable(false);
    self.tagPresent = ko.observable(false);
    self.tagSpoolId = ko.observable(null); // spool id already stored on the tag, if any
    self.tagSpoolDisplayName = ko.observable(null);
    self.tagValues = ko.observable(null); // raw "extended" payload of the tag currently on the reader, if any
    self.errorMessage = ko.observable(null);
    self.isWriting = ko.observable(false);
    self.writeSucceeded = ko.observable(false);
    self.overwriteConfirmed = ko.observable(false);
    // Set once the user confirmed overwriting a tag that looks like a manufacturer tag.
    // Reset on every tag change, exactly like overwriteConfirmed - a confirmation given
    // for one tag must never carry over to the next one placed on the reader.
    self.foreignTagConfirmed = ko.observable(false);

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

    // What the firmware makes of the data already on the tag: "empty", "foreign" or ""
    // (unknown/not reported). "foreign" means it found data in a format it does not
    // recognize - almost certainly another vendor's tag. Only reported for Mifare Classic
    // and NTAG; NFC-V and older firmware answer "", which is why isPossiblyForeignTag
    // below keeps a heuristic of its own rather than trusting this field alone.
    self.tagOccupancy = ko.observable("");

    // Result of the last completed write, once the async write finishes.
    self.writeFormatUsed = ko.observable(null);
    self.writeBytesWritten = ko.observable(null);
    self.writeDroppedFields = ko.observableArray([]);
    // Fields the format cannot hold at all, as opposed to writeDroppedFields (cut for lack
    // of room). Only ever populated for fields the spool actually had a value in, so the
    // list names what was really lost rather than everything the format happens to omit.
    self.writeUnsupportedFields = ko.observableArray([]);
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

    // A manufacturer tag (Bambu, Creality, Anycubic, ...) carries no SpoolManager id and no
    // OctoScale payload, so before this existed it was indistinguishable from a blank tag
    // and canWrite() happily allowed a write that destroys it irreversibly.
    //
    // Two sources, deliberately kept side by side:
    //  - the firmware's own verdict (occupancy "foreign"), which is precise but only
    //    reported for Mifare Classic and NTAG,
    //  - a heuristic for everything else: NFC-V, and any firmware too old to send the
    //    field at all. It also flags genuinely blank tags, which is the right direction to
    //    err in for a safeguard - a needless confirmation costs a click, a missing one
    //    costs the tag.
    // occupancy "empty" is the firmware positively confirming the tag is blank, so it wins
    // over the heuristic and no warning is shown.
    self.isPossiblyForeignTag = ko.pureComputed(function () {
        if (self.tagPresent() != true) {
            return false;
        }
        var occupancy = self.tagOccupancy();
        if (occupancy === "foreign") {
            return true;
        }
        if (occupancy === "empty") {
            return false;
        }
        return (
            self.hasExtendedData() !== true &&
            self.tagSpoolId() == null &&
            self.tagType() != null &&
            self.tagType() != "unknown"
        );
    });

    // True when the firmware itself flagged the tag - lets the UI say "is a vendor tag"
    // instead of the hedged "may be one" the heuristic can only justify.
    self.isConfirmedForeignTag = ko.pureComputed(function () {
        return self.tagPresent() == true && self.tagOccupancy() === "foreign";
    });

    // The firmware names the fields as they appear in the write payload
    // ("dryingTemperature"); the diff table already holds the labels a user knows, so reuse
    // those rather than showing raw keys.
    self.writeUnsupportedFieldLabels = ko.pureComputed(function () {
        var labelsByKey = {};
        OCTOSCALE_TAG_DIFF_FIELDS.forEach(function (field) {
            labelsByKey[field.key] = field.label;
        });
        OCTOSCALE_TAG_DIFF_DATE_FIELDS.forEach(function (field) {
            labelsByKey[field.key] = field.label;
        });
        return self.writeUnsupportedFields().map(function (key) {
            return labelsByKey[key] || key;
        });
    });

    self.foreignTagWarningText = ko.pureComputed(function () {
        if (self.isPossiblyForeignTag() != true) {
            return "";
        }
        if (self.isConfirmedForeignTag()) {
            return "This tag holds data in a format OctoScale does not recognize - most likely a manufacturer tag (Bambu, Creality, ...). Writing would destroy it irreversibly.";
        }
        return "This tag may be a manufacturer tag (Bambu, Creality, ...) rather than a blank one. Writing would destroy it irreversibly.";
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
            var tagValue = octoScaleNormalizeTagValue(
                tagValues[field.key],
                field.tagValueDivisor
            );
            var currentValue =
                typeof spoolItem[field.key] === "function"
                    ? spoolItem[field.key]()
                    : null;
            if (
                !octoScaleValuesDiffer(
                    tagValue,
                    currentValue,
                    field.caseInsensitive
                )
            ) {
                return;
            }
            diffs.push({
                label: field.label,
                oldValueText: octoScaleFormatDiffValue(tagValue, field.unit),
                newValueText: octoScaleFormatDiffValue(currentValue, field.unit)
            });
        });

        OCTOSCALE_TAG_DIFF_DATE_FIELDS.forEach(function (field) {
            var hasDayField = Object.prototype.hasOwnProperty.call(
                tagValues,
                field.key
            );
            if (!hasDayField) {
                // The minute-of-day field is meaningless without its day field.
                return;
            }
            var tagValueText = octoScaleTagDateText(tagValues, field);
            var currentValueText =
                typeof spoolItem[field.key] === "function"
                    ? spoolItem[field.key]()
                    : null;
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
        // Independent of the id-based confirmation above: a manufacturer tag carries no id
        // at all, so needsOverwriteConfirmation() never fires for it.
        if (self.isPossiblyForeignTag() && self.foreignTagConfirmed() != true) {
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
                var previousUid = self.tagUid();
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
                    responseData.present === true && responseData.hasExtendedData === true
                );
                self.tagValues(
                    responseData.present === true ? responseData.extended || null : null
                );
                self.tagOccupancy(
                    responseData.present === true ? responseData.occupancy || "" : ""
                );
                // a different tag on the reader invalidates a confirmation given for the previous one
                if (previousSpoolId != self.tagSpoolId()) {
                    self.overwriteConfirmed(false);
                }
                // The foreign-tag confirmation has to key off the UID instead: a
                // manufacturer tag carries no spool id, so tagSpoolId() stays null across a
                // tag swap and the check above would never notice it changed.
                if (previousUid != self.tagUid()) {
                    self.foreignTagConfirmed(false);
                    // ...and a read result belongs to the tag it was read from. Showing it
                    // next to a different tag would invite applying the wrong values.
                    self.readTagResult(null);
                    self.readTagError(null);
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
        self.foreignTagConfirmed(false);
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
        self.foreignTagConfirmed(false);
        self.tagOccupancy("");
        self.errorMessage(null);
        self.teachInResult(null);
        self.isTeachingIn(false);
        self.isReadingTag(false);
        self.readTagResult(null);
        self.readTagError(null);
    };

    self.confirmOverwrite = function () {
        self.overwriteConfirmed(true);
    };

    self.confirmForeignTagOverwrite = function () {
        self.foreignTagConfirmed(true);
    };

    // --- reading a vendor tag -------------------------------------------------------
    // Reflects the settings toggle. Read live rather than captured once: the setting can be
    // switched while a dialog is open, and the backend enforces it independently anyway.
    self.tagReadingEnabled = ko.pureComputed(function () {
        var settings = self.pluginSettings;
        if (settings == null || settings.octoScaleTagReadingEnabled == null) {
            return false;
        }
        var value = settings.octoScaleTagReadingEnabled;
        return (typeof value === "function" ? value() : value) === true;
    });
    self.isReadingTag = ko.observable(false);
    self.readTagResult = ko.observable(null); // the whole /octoscale/readTag answer
    self.readTagError = ko.observable(null);

    self.canReadTag = ko.pureComputed(function () {
        return (
            self.tagReadingEnabled() === true &&
            self.tagPresent() === true &&
            self.isReadingTag() !== true &&
            self.isWriting() !== true
        );
    });

    // Offered for any tag on the reader, not just a suspected vendor one.
    //
    // This was originally gated on isPossiblyForeignTag(), on the reasoning that reading is
    // the constructive alternative to overwriting a manufacturer tag. That turned out too
    // narrow: it also hides the button for a tag SpoolManager wrote itself, so "show me
    // what is actually on this tag" was impossible - including for verifying a write.
    // Reading never modifies anything, so there is no reason to withhold it.
    self.shouldOfferTagRead = ko.pureComputed(function () {
        return self.tagReadingEnabled() === true && self.tagPresent() === true;
    });

    self.readTagValues = ko.pureComputed(function () {
        var result = self.readTagResult();
        if (result == null || result.parsed !== true || result.fields == null) {
            return [];
        }
        // Reuses the write path's field table and formatting: the keys the backend emits
        // are deliberately the same ones a write would put on the tag.
        //
        // No tagValueDivisor here, unlike tagValueDiff above: these values come from
        // /octoscale/readTag, which already converted them into SpoolManager's own units.
        // Dividing again would turn 8 hours into 8 minutes.
        var rows = [];
        OCTOSCALE_TAG_DIFF_FIELDS.forEach(function (field) {
            if (!Object.prototype.hasOwnProperty.call(result.fields, field.key)) {
                return;
            }
            rows.push({
                label: field.label,
                valueText: octoScaleFormatDiffValue(
                    octoScaleNormalizeTagValue(result.fields[field.key]),
                    field.unit
                )
            });
        });
        return rows;
    });

    self.readTag = function () {
        if (self.canReadTag() == false) {
            return;
        }
        self.isReadingTag(true);
        self.readTagResult(null);
        self.readTagError(null);

        self.apiClient.readOctoScaleTag(function (responseData) {
            if (self.isActive() == false) {
                return;
            }
            self.isReadingTag(false);
            if (!responseData || responseData.success !== true) {
                self.readTagError(
                    responseData && responseData.error
                        ? responseData.error
                        : "Could not read the tag."
                );
                return;
            }
            self.readTagResult(responseData);
            if (responseData.parsed !== true) {
                self.readTagError(
                    responseData.error || "This tag's format was not recognized."
                );
            }
        });
    };

    self.clearReadTagResult = function () {
        self.readTagResult(null);
        self.readTagError(null);
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
                            "Could not teach in the tag UID."
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
                            "Could not teach in the tag UID."
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
        self.writeUnsupportedFields([]);
        self.writeWarning(null);
        self.teachInResult(null);

        self.apiClient.writeOctoScaleTag(
            self.targetDatabaseId(),
            function (responseData) {
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
                    // The firmware refused rather than failed. "foreign tag" is its own
                    // safeguard against destroying a vendor tag - surfacing the confirm button
                    // turns a dead end into a decision the user can make. "write in progress"
                    // is transient and just needs another attempt.
                    if (responseData && responseData.overridable === true) {
                        self.foreignTagConfirmed(false);
                    }
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
                            self.writeUnsupportedFields(
                                statusData.unsupportedFields || []
                            );
                            self.writeWarning(statusData.warning || null);
                            self.teachRfidTagKeyIfNeeded(statusData);
                        } else {
                            self.writeSucceeded(false);
                            self.errorMessage(
                                statusData.error || "Could not write the tag."
                            );
                        }
                    });
                }, OCTOSCALE_WRITE_STATUS_POLL_INTERVAL_MS);
            },
            self.foreignTagConfirmed() === true
        );
    };
}
