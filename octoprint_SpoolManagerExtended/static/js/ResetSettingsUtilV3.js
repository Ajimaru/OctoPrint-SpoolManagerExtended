// Modernisation of this file (const over var, async/await, extracted settings-reset
// helper, simplified jQuery selectors) and the idea of asking for confirmation before
// resetting were adopted from mdziekon/OctoPrint-SpoolManager PR #19 (GH-18).
//
// The implementation deliberately differs from that PR: his fork's backend exposes
// "isResetSettingsEnabled" and "resetSettings" actions and persists the reset server-side,
// whereas this fork only exposes "getDefaultSettings" and resets in-memory observables that
// the user still has to save. The button-gating request from PR #19 is therefore omitted,
// and the confirmation wording reflects the non-persistent semantics.
//
// Trying to use OctoPrint's showConfirmationDialog() here instead of the PR's plain confirm()
// does not work — see the comment at the reset button's click handler below.
function ResetSettingsUtilV3(pluginSettings) {
    const pluginSettingsFromPlugin = pluginSettings;

    const RESET_BUTTON_ID = "resetSettingsButton";
    const RESET_BUTTON_HTML = `<button id="${RESET_BUTTON_ID}" class="btn btn-warning" style="margin-right:3%">Reset Settings</button>`;
    const RESET_BUTTON_SELECTOR = `#${RESET_BUTTON_ID}`;

    // Extracted from the inline reset loop, following mdziekon/OctoPrint-SpoolManager PR #19
    // (GH-18). Handles three shapes of default values, only one level of nesting:
    //  - "excludedFromTemplateCopy": an observableArray, needs removeAll()/push()
    //  - any other object: nested observables, set them individually
    //  - scalars: set the observable directly
    //
    // Not every key returned by the backend's getDefaultSettings is a Knockout observable in
    // the settings view model. "installed_version" for example is bound as plain text and is
    // never persisted, and it happens to be the FIRST key in the payload. Calling it like an
    // observable threw a TypeError on the very first iteration, so the whole reset silently
    // did nothing (the previous implementation had no try/catch, so the throw disappeared
    // inside jQuery's .done()). Anything that is not an observable is therefore skipped.
    const resetPluginSettings = (pluginSettingsStorage, newSettings) => {
        Object.entries(newSettings).forEach(([key, value]) => {
            const target = pluginSettingsStorage[key];

            if (!ko.isObservable(target)) {
                return;
            }

            if (key === "excludedFromTemplateCopy") {
                target.removeAll();
                value.forEach((excludedPropName) => {
                    target.push(excludedPropName);
                });

                return;
            }

            if (typeof value !== "object" || !value) {
                target(value);

                return;
            }

            Object.entries(value).forEach(([nestedKey, nestedValue]) => {
                if (!ko.isObservable(target[nestedKey])) {
                    return;
                }

                // Previously this passed the whole parent object instead of the nested value;
                // corrected while extracting, matching mdziekon's version in PR #19 (GH-18).
                target[nestedKey](nestedValue);
            });
        });
    };

    this.assignResetSettingsFeature = function (
        PLUGIN_ID_string,
        mapSettingsToViewModel_function
    ) {
        /**
         * NOTE (from mdziekon/OctoPrint-SpoolManager PR #19, GH-18): PrintJobHistory uses the
         * same name ("resetSettingsButtonFunction") to check for event listener existence.
         * Eventually these two should live their separate lives and not depend on one another,
         * but for now the name is kept for compatibility's sake.
         */
        const resetSettingsButtonFunction = () => {
            $(RESET_BUTTON_SELECTOR).hide();
        };

        // hide reset button when hiding settings. needed because of next dialog-shown event
        const $settingsDialog = $("#settings_dialog");
        const settingsDialogDOMElement = $settingsDialog.get(0);

        const eventObject = $._data(settingsDialogDOMElement, "events");
        if (
            !eventObject ||
            !eventObject.hide ||
            eventObject.hide[0].handler.name !== resetSettingsButtonFunction.name
        ) {
            $settingsDialog.on("hide", resetSettingsButtonFunction);
        }

        const $settingsTabs = $("#settingsTabs");

        // add click hook for own plugin to check if resetSettings is available
        // Prefix match (^=) is kept from the original rather than PR #19's exact match: some
        // OctoPrint versions suffix the tab href (e.g. "#settings_plugin_SpoolManager_link"),
        // and an exact match would silently disable the whole feature there.
        const pluginSettingsLink = $settingsTabs.find(
            `a[href^="#settings_plugin_${PLUGIN_ID_string}"]:not([hooked="${PLUGIN_ID_string}"])`
        );
        pluginSettingsLink.attr("hooked", PLUGIN_ID_string);
        pluginSettingsLink.click(function () {
            // noinspection JSJQueryEfficiency - result changes after HTML insertion
            let resetButton = $(RESET_BUTTON_SELECTOR);
            // build-button, if necessary
            if (resetButton.length === 0) {
                // add button to page
                $(".modal-footer > .aboutlink").after(RESET_BUTTON_HTML);
                resetButton = $(RESET_BUTTON_SELECTOR);
            }

            // add/update click action
            resetButton.unbind("click");
            resetButton.click(async function () {
                // Confirmation before resetting, adopted from mdziekon/OctoPrint-SpoolManager
                // PR #19 (GH-18).
                //
                // This deliberately uses the native, blocking confirm() like the original PR does,
                // and NOT OctoPrint's showConfirmationDialog() / SPOOLMANAGER_DIALOGS.confirmDanger():
                // this button is injected into the settings dialog's own .modal-footer, and opening a
                // second Bootstrap 2 modal on top fires "hide" on #settings_dialog. That hide is what
                // resetSettingsButtonFunction listens to, so the button was torn down mid-click and
                // onproceed never ran. This is the one spot that intentionally keeps a native confirm().
                const hasConfirmed = confirm(
                    "Reset all SpoolManager plugin settings to their default values?\n\n" +
                        "The change is only applied in the UI and takes effect once you save the settings."
                );

                if (!hasConfirmed) {
                    return;
                }

                try {
                    const newSettingsData = await $.ajax({
                        url: `${API_BASEURL}plugin/${PLUGIN_ID_string}?action=getDefaultSettings`,
                        type: "GET"
                    });

                    // reset all values in the in-memory storage
                    resetPluginSettings(pluginSettingsFromPlugin, newSettingsData);

                    // delegate to the client. So client is able to reset/init other values
                    mapSettingsToViewModel_function(newSettingsData);

                    // Success is only reported once the reset actually happened: reporting
                    // it before the loop meant a failing reset still showed "restored!".
                    SPOOLMANAGER_DIALOGS.notify({
                        title: "Default settings restored!",
                        message:
                            "The plugin settings have been reset but not yet been saved.<br>Remember to save. If you reset the settings accidentally, you can reload the page to revert.",
                        type: "info"
                    });
                } catch (error) {
                    // Error handling adopted from mdziekon/OctoPrint-SpoolManager PR #19
                    // (GH-18): previously a failing request did nothing at all.
                    console.error("ERROR: Plugin settings reset", error);

                    SPOOLMANAGER_DIALOGS.notify({
                        title: "Plugin settings reset",
                        message:
                            "An error occurred while loading the default settings. The settings have not been changed.",
                        type: "error"
                    });
                }
            });

            resetButton.show();
        });

        // default behaviour -> hide reset button --> if not already assigned
        const otherSettingsLink = $settingsTabs.find(
            `a[href^="#settings_"]:not([hooked])`
        );
        if (otherSettingsLink.length !== 0) {
            otherSettingsLink.attr("hooked", "otherSettings");
            otherSettingsLink.click(resetSettingsButtonFunction);
        }
    };
}
