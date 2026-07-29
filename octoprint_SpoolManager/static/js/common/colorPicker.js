// Color picker component, replacing the pick-a-color jQuery widget (unmaintained since 2014).
//
// The widget bound itself to a fixed DOM id, which is why the Add Spool Wizard could not use it:
// a second editable SpoolItem would have hijacked the edit dialog's three pickers, so the wizard
// fell back to native <input type="color"> - and users saw two different pickers depending on
// which dialog they opened. This component takes the container element instead of an id, so any
// number of instances can live side by side and both dialogs look the same.
//
// It also drops three workarounds the widget needed:
//   - the translucent swatch (its checkerboard background made the widget derive #000000, needing
//     a mousedown/setTimeout guard) is gone; "colorless" is a checkbox in both dialogs now
//   - values carry their leading "#" throughout, so no prefixing/substr(1) on every sync
//   - setColor() writes through to the preview, so the settings-reset DOM hack is unnecessary
//
// Depends on tinycolor for hex <-> HSL conversion and on SPOOLMANAGER_CONSTANTS.COLORS.PALETTE
// for the swatches.
/**
 * Defined without specifier to be globally accessible
 */
SPOOLMANAGER_COLOR_PICKER = {

    // number of swatches per row; also drives the menu width via CSS
    GRID_COLUMNS: 8,

    _instanceCounter: 0,

    /**
     * Creates a picker inside the given container.
     *
     * @param {HTMLElement|jQuery|string} container element (or selector) the picker is rendered into
     * @param {Object} [options] { initialColor: "#rrggbb" }
     * @returns {Object} { selectedColor: ko.observable, destroy: function }
     */
    create: function (container, options) {
        var settings = options || {};
        var $container = $(container);
        if ($container.length === 0) {
            console.warn("SpoolManager: color picker container not found", container);
            return { selectedColor: ko.observable(settings.initialColor || null), destroy: function () {} };
        }

        var self = {};
        var instanceId = ++SPOOLMANAGER_COLOR_PICKER._instanceCounter;
        var initialColor = SPOOLMANAGER_COLOR_PICKER._normalizeHex(
            settings.initialColor || SPOOLMANAGER_CONSTANTS.COLORS.DEFAULT
        );

        self.selectedColor = ko.observable(initialColor);

        // guards the observable -> UI sync against the UI -> observable write it triggers
        var applyingColor = false;

        ///////////////////////////////////////////////////////////////////////////////// MARKUP

        var $root = $('<span class="spm-color-picker"></span>');
        var $toggle = $(
            '<button type="button" class="btn spm-color-picker-toggle">' +
                '<span class="color-preview spm-color-picker-current"></span>' +
                '<span class="caret"></span>' +
            "</button>"
        );
        var $menu = $('<div class="dropdown-menu spm-color-picker-menu"></div>');

        // Tabs. Bootstrap's tab plugin targets elements by id, which is exactly the coupling this
        // component avoids, so the two panels are toggled by hand.
        var basicTabId = "spm-color-basic-" + instanceId;
        var advancedTabId = "spm-color-advanced-" + instanceId;
        var $tabs = $(
            '<ul class="nav nav-tabs spm-color-picker-tabs">' +
                '<li class="active"><a href="#" data-spm-tab="' + basicTabId + '">Basic Colors</a></li>' +
                '<li><a href="#" data-spm-tab="' + advancedTabId + '">Advanced</a></li>' +
            "</ul>"
        );

        // --- basic tab: swatch grid + hex input
        var $basicPanel = $('<div class="spm-color-picker-panel active" data-spm-panel="' + basicTabId + '"></div>');
        var $grid = $('<div class="spm-color-picker-grid"></div>');
        var palette = SPOOLMANAGER_CONSTANTS.COLORS.PALETTE;
        for (var i = 0; i < palette.length; i++) {
            $('<button type="button" class="spm-swatch"></button>')
                .attr("title", palette[i].name)
                .attr("data-spm-hex", palette[i].hex)
                .css("background-color", palette[i].hex)
                .appendTo($grid);
        }
        var $hexRow = $('<div class="spm-color-picker-hex-row"></div>');
        var $hexInput = $('<input type="text" class="spm-color-picker-hex" spellcheck="false" maxlength="7">');
        $hexRow.append($('<span class="spm-color-picker-hex-label">Hex</span>')).append($hexInput);
        $basicPanel.append($grid).append($hexRow);

        // --- advanced tab: HSL sliders + preview
        // Native range inputs instead of the widget's hand-rolled drag bands: same result, but
        // they work on touch and with the keyboard, and the gradient still goes in the track.
        var $advancedPanel = $('<div class="spm-color-picker-panel" data-spm-panel="' + advancedTabId + '"></div>');
        var buildSlider = function (label, max) {
            var $row = $('<div class="spm-color-picker-slider-row"></div>');
            var $label = $('<span class="spm-color-picker-slider-label"></span>').text(label + ": ");
            var $value = $('<span class="spm-color-picker-slider-value"></span>');
            var $input = $('<input type="range" class="spm-color-picker-slider" min="0" step="1">').attr("max", max);
            $row.append($label.append($value)).append($input);
            return { row: $row, input: $input, value: $value };
        };
        var hueSlider = buildSlider("Hue", 360);
        var saturationSlider = buildSlider("Saturation", 100);
        var lightnessSlider = buildSlider("Lightness", 100);
        var $preview = $('<div class="spm-color-picker-preview"></div>');
        $advancedPanel
            .append(hueSlider.row)
            .append(saturationSlider.row)
            .append(lightnessSlider.row)
            .append($('<div class="spm-color-picker-preview-label">Preview</div>'))
            .append($preview);

        $menu.append($tabs).append($basicPanel).append($advancedPanel);
        $root.append($toggle).append($menu);
        $container.empty().append($root);

        ///////////////////////////////////////////////////////////////////////////////// SYNC

        // Pushes a color into every part of the UI. Called for programmatic changes as well as
        // for user input, so the swatch grid, hex field, sliders and both previews never drift
        // apart - the old widget only updated the part the user touched.
        var renderColor = function (hexValue, options) {
            var skipHexInput = options != null && options.skipHexInput === true;
            var skipSliders = options != null && options.skipSliders === true;

            $toggle.find(".spm-color-picker-current").css("background-color", hexValue);
            $preview.css("background-color", hexValue);

            if (skipHexInput == false) {
                $hexInput.val(hexValue);
            }

            var normalizedValue = hexValue.toLowerCase();
            $grid.find(".spm-swatch").each(function () {
                var $swatch = $(this);
                $swatch.toggleClass("selected", $swatch.attr("data-spm-hex").toLowerCase() === normalizedValue);
            });

            var hsl = tinycolor(hexValue).toHsl();
            var hue = Math.round(hsl.h);
            var saturation = Math.round(hsl.s * 100);
            var lightness = Math.round(hsl.l * 100);

            if (skipSliders == false) {
                hueSlider.input.val(hue);
                saturationSlider.input.val(saturation);
                lightnessSlider.input.val(lightness);
            }
            hueSlider.value.text(hue);
            saturationSlider.value.text(saturation + "%");
            lightnessSlider.value.text(lightness + "%");

            // the tracks preview what dragging would do: each shows the current color with only
            // its own component swept across the range
            // background-image rather than the "background" shorthand: the shorthand would also
            // reset background-color and is rejected outright by stricter CSS parsers
            hueSlider.input.css("background-image", SPOOLMANAGER_COLOR_PICKER._hueGradient(saturation, lightness));
            saturationSlider.input.css("background-image", SPOOLMANAGER_COLOR_PICKER._sweepGradient(
                function (step) { return { h: hue, s: step, l: lightness }; }
            ));
            lightnessSlider.input.css("background-image", SPOOLMANAGER_COLOR_PICKER._sweepGradient(
                function (step) { return { h: hue, s: saturation, l: step }; }
            ));
        };

        // single funnel for every user-driven change
        var commitColor = function (hexValue, renderOptions) {
            var normalized = SPOOLMANAGER_COLOR_PICKER._normalizeHex(hexValue);
            if (normalized == null) {
                return;
            }
            applyingColor = true;
            try {
                renderColor(normalized, renderOptions);
                self.selectedColor(normalized);
            } finally {
                applyingColor = false;
            }
        };

        // sync: observable -> UI (programmatic changes, e.g. loading a spool into the dialog)
        var colorSubscription = self.selectedColor.subscribe(function (newColor) {
            if (applyingColor == true) {
                return;
            }
            var normalized = SPOOLMANAGER_COLOR_PICKER._normalizeHex(newColor);
            if (normalized == null) {
                return;
            }
            applyingColor = true;
            try {
                renderColor(normalized);
                // write the normalized form back so consumers always see "#rrggbb"
                if (normalized !== newColor) {
                    self.selectedColor(normalized);
                }
            } finally {
                applyingColor = false;
            }
        });

        ///////////////////////////////////////////////////////////////////////////////// EVENTS

        var closeMenu = function () {
            $root.removeClass("open");
        };

        $toggle.on("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var wasOpen = $root.hasClass("open");
            // only one picker open at a time, including pickers of other instances
            $(".spm-color-picker").removeClass("open");
            if (wasOpen == false) {
                $root.addClass("open");
            }
        });

        // Clicks inside the menu must not bubble: the menu holds form controls, and Bootstrap 2's
        // own dropdown handling (plus the outside-click handler below) would close it on every
        // slider drag.
        $menu.on("click", function (event) {
            event.stopPropagation();
        });

        $tabs.on("click", "a", function (event) {
            event.preventDefault();
            var targetPanel = $(this).attr("data-spm-tab");
            $tabs.find("li").removeClass("active");
            $(this).parent().addClass("active");
            $menu.find(".spm-color-picker-panel").each(function () {
                $(this).toggleClass("active", $(this).attr("data-spm-panel") === targetPanel);
            });
        });

        $grid.on("click", ".spm-swatch", function (event) {
            event.preventDefault();
            commitColor($(this).attr("data-spm-hex"));
        });

        // Typing is only committed once it parses, so a half-typed "#ff" does not reset the
        // color; blur re-renders to undo anything that never became valid.
        $hexInput.on("input", function () {
            var typedValue = $hexInput.val();
            if (SPOOLMANAGER_COLOR_PICKER._normalizeHex(typedValue) != null) {
                commitColor(typedValue, { skipHexInput: true });
            }
        });
        $hexInput.on("blur", function () {
            renderColor(self.selectedColor());
        });
        $hexInput.on("keydown", function (event) {
            if (event.which === 13) {
                event.preventDefault();
                renderColor(self.selectedColor());
            }
        });

        var onSliderInput = function () {
            var hexValue = tinycolor({
                h: parseInt(hueSlider.input.val(), 10),
                s: parseInt(saturationSlider.input.val(), 10) / 100,
                l: parseInt(lightnessSlider.input.val(), 10) / 100,
            }).toHexString();
            // the dragged slider keeps its own position; re-setting .val() mid-drag would fight
            // the browser, and rounding through HSL can shift the other two by a step
            commitColor(hexValue, { skipSliders: true });
        };
        hueSlider.input.on("input change", onSliderInput);
        saturationSlider.input.on("input change", onSliderInput);
        lightnessSlider.input.on("input change", onSliderInput);

        // namespaced so destroy() can take exactly these handlers back off the document
        var documentNamespace = ".spmColorPicker" + instanceId;
        $(document).on("click" + documentNamespace, function () {
            closeMenu();
        });
        $(document).on("keydown" + documentNamespace, function (event) {
            if (event.which === 27 && $root.hasClass("open")) {
                closeMenu();
            }
        });

        self.destroy = function () {
            $(document).off(documentNamespace);
            colorSubscription.dispose();
            $root.remove();
        };

        renderColor(initialColor);

        return self;
    },

    ////////////////////////////////////////////////////////////////////////////////////// HELPERS

    // Accepts "#rrggbb", "rrggbb", "#rgb" and color names; returns "#rrggbb" or null.
    _normalizeHex: function (colorValue) {
        if (colorValue == null) {
            return null;
        }
        var rawValue = ("" + colorValue).trim();
        if (rawValue.length === 0) {
            return null;
        }
        var parsed = tinycolor(rawValue);
        if (parsed.isValid() == false) {
            return null;
        }
        return parsed.toHexString();
    },

    // 0..360 in fixed steps, so the hue track shows the full wheel at the current S/L
    _hueGradient: function (saturation, lightness) {
        return SPOOLMANAGER_COLOR_PICKER._sweepGradient(function (step) {
            return { h: step * 3.6, s: saturation, l: lightness };
        });
    },

    // Builds a "linear-gradient(to right, ...)" from 11 samples (0%, 10%, ... 100%). The callback
    // gets the percentage and returns the HSL for that stop; s/l are given in percent.
    _sweepGradient: function (hslForStep) {
        var stops = [];
        for (var step = 0; step <= 100; step += 10) {
            var hsl = hslForStep(step);
            stops.push(tinycolor({ h: hsl.h, s: hsl.s / 100, l: hsl.l / 100 }).toHexString() + " " + step + "%");
        }
        return "linear-gradient(to right, " + stops.join(", ") + ")";
    },
};
