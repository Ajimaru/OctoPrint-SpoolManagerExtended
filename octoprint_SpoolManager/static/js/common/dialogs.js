// Shared dialog/notification helpers.
//
// Replaces the native confirm()/alert() popups that were scattered across the plugin: those are
// unstyled, cannot be themed and look foreign inside OctoPrint. Everything routes through
// OctoPrint's own showConfirmationDialog() (Bootstrap 2 modal) resp. PNotify, so the plugin
// matches the host UI.
//
// The API is promise-based on purpose: `if (confirm(...)) { ... }` maps to
// `.confirm(...).then(function(ok){ if (!ok) return; ... })` without restructuring the call site
// beyond one level of nesting.
/**
 * Defined without specifier to be globally accessible
 */
SPOOLMANAGER_DIALOGS = {
    // Bootstrap 2 renders the message as HTML - escape anything that comes from spool data.
    escapeHtml: function (value) {
        if (value == null) {
            return "";
        }
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    },

    /**
     * Builds an <ul> from an array of already-escaped (or trusted) strings.
     * Used for the "these tools/spools are affected" enumerations that previously
     * were "\n- " joined plain text.
     */
    buildHtmlList: function (items) {
        if (items == null || items.length == 0) {
            return "";
        }
        var listItems = items.map(function (item) {
            return "<li>" + item + "</li>";
        });
        return "<ul class='spm-dialog-list'>" + listItems.join("") + "</ul>";
    },

    /**
     * Internal: wraps showConfirmationDialog in a promise.
     * Resolves with the pressed proceed-button index, or null when cancelled/dismissed.
     * Never rejects, so call sites do not need a .catch().
     */
    _confirmationDialog: function (options) {
        return new Promise(function (resolve) {
            if (typeof showConfirmationDialog !== "function") {
                // Fallback for environments without OctoPrint's helper (e.g. plain unit tests).
                var plainText = [options.title, options.message, options.question]
                    .filter(function (part) {
                        return part != null && part != "";
                    })
                    .join("\n\n")
                    .replace(/<[^>]+>/g, "");
                resolve(confirm(plainText) ? 0 : null);
                return;
            }

            var settled = false;
            var settle = function (value) {
                if (settled == true) {
                    return;
                }
                settled = true;
                resolve(value);
            };

            showConfirmationDialog({
                title: options.title,
                message: options.message,
                question: options.question,
                cancel: options.cancel != null ? options.cancel : "Cancel",
                proceed: options.proceed != null ? options.proceed : "Proceed",
                proceedClass:
                    options.proceedClass != null ? options.proceedClass : "primary",
                onproceed: function (buttonIndex) {
                    // fires before the modal is hidden, so this wins over the onclose fallback below
                    settle(buttonIndex == null ? 0 : buttonIndex);
                },
                // NOT oncancel: OctoPrint only calls that for the cancel *button*, not for the
                // header "x", ESC or a backdrop click - the promise would never settle. onclose
                // is bound to the modal's "hidden" event and therefore covers every dismissal.
                onclose: function () {
                    settle(null);
                },
                // consistent with the pre-existing showConfirmationDialog usages in this plugin:
                // avoids Bootstrap 2 fade/backdrop stacking glitches when a dialog opens above a dialog
                nofade: true
            });
        });
    },

    /**
     * Yes/no confirmation.
     * @returns {Promise<Boolean>} true when the user proceeded
     */
    confirm: function (options) {
        return this._confirmationDialog(options).then(function (buttonIndex) {
            return buttonIndex != null;
        });
    },

    /**
     * Confirmation for destructive actions (delete, replace, reset). Same as confirm(),
     * but the proceed button is rendered in red.
     * @returns {Promise<Boolean>} true when the user proceeded
     */
    confirmDanger: function (options) {
        var dangerOptions = $.extend({}, options, {proceedClass: "danger"});
        return this.confirm(dangerOptions);
    },

    /**
     * Confirmation with multiple proceed buttons (`proceed` is an array of labels).
     * @returns {Promise<Number|null>} index of the pressed button, null when cancelled
     */
    choose: function (options) {
        return this._confirmationDialog(options);
    },

    /**
     * Replacement for alert(): a non-blocking PNotify toast.
     *
     * Dedupes identical toasts by tagging them with a slug built from title+message - otherwise
     * repeated validation errors stack up. Logic moved here from SpoolManager.showPopUp().
     *
     * @param {Object} options - {title, message, type: "info"|"success"|"error", autoclose}
     */
    notify: function (options) {
        var type = options.type != null ? options.type : "info";
        var title =
            type.toUpperCase() + ": " + (options.title != null ? options.title : "");
        var message = options.message != null ? options.message : "";
        var popupId = (title + message).replace(/([^a-z0-9]+)/gi, "-");
        // errors stay until dismissed, everything else auto-hides unless told otherwise
        var autoclose = options.autoclose != null ? options.autoclose : type != "error";

        if ($("." + popupId).length < 1) {
            new PNotify({
                title: "SPM:" + title,
                text: message,
                type: type,
                hide: autoclose,
                addclass: popupId
            });
        }
    },

    // Sticky notification carrying action buttons. Used where the user has to choose
    // what happens next (e.g. an unknown RFID tag: create a spool, edit one, or ignore)
    // and nothing may open on its own - the triggering event is pushed to *every*
    // connected browser, so auto-opening a dialog would pop it up on all of them.
    //
    // buttons: [{text, addClass, onClick}]. Returns the PNotify instance, or null when a
    // notification with the same identity is already on screen (prevents stacking when
    // several channels report in quick succession).
    notifyWithActions: function (options) {
        var type = options.type != null ? options.type : "info";
        var title = options.title != null ? options.title : "";
        var message = options.message != null ? options.message : "";
        var identity = options.identity != null ? options.identity : title + message;
        var popupId = ("spm-action-" + identity).replace(/([^a-z0-9]+)/gi, "-");

        if ($("." + popupId).length > 0) {
            return null;
        }

        var buttonDefinitions = options.buttons || [];
        var notice = new PNotify({
            title: "SPM: " + title,
            text: message,
            type: type,
            hide: false,
            addclass: popupId,
            confirm: {
                confirm: true,
                buttons: buttonDefinitions.map(function (button) {
                    return {
                        text: button.text,
                        addClass: button.addClass || "btn-small",
                        click: function (notice) {
                            notice.remove();
                            if (typeof button.onClick === "function") {
                                button.onClick();
                            }
                        }
                    };
                })
            },
            buttons: {
                closer: true,
                sticker: false
            }
        });
        return notice;
    }
};
