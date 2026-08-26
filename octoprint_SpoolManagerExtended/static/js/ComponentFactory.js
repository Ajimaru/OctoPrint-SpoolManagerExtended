// Static factory methods adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10):
// the component creators are stateless, so they live on ComponentFactory itself and the
// constructor only delegates (prevents needless instantiation & coupling). The method
// bodies are our own (extended color palette, translucent swatch, datetimepicker stub).
function ComponentFactory(pluginId) {
    this.pluginId = pluginId;

    this.createDateTimePicker = ComponentFactory.createDateTimePicker;
    this.createColorPicker = ComponentFactory.createColorPicker;
    this.createNoteEditor = ComponentFactory.createNoteEditor;
}

////////////////////////////////////////////////////////////////////////////////////////////////// DATETIME - PICKER
// jQuery datetimepicker removal authored by @mdziekon, adopted via mdziekon/OctoPrint-SpoolManager PR #21.
// The dialog uses native date/datetime-local inputs; this only provides the observables
// the edit dialog binds against.
ComponentFactory.createDateTimePicker = function (elementId, showTimePicker) {
    var componentViewModel = {
        currentDateTime: ko.observable(),
        isEnabled: ko.observable(true)
    };

    return componentViewModel;
};

///////////////////////////////////////////////////////////////////////////////////// COLOR - PICKER
// Thin adapter over SPOOLMANAGER_COLOR_PICKER (see common/colorPicker.js), which replaced the
// unmaintained pick-a-color widget. The component itself takes a container element, but the
// callers here still identify their picker by id, so the lookup stays in one place.
//
// The returned model is the same one the widget version returned - consumers only ever used
// componentViewModel.selectedColor, and that observable now carries "#rrggbb" consistently
// (pick-a-color stored the hex without the "#", hence the prefixing this used to do).
ComponentFactory.createColorPicker = function (elementId, initialColor) {
    var picker = SPOOLMANAGER_COLOR_PICKER.create("#" + elementId, {
        initialColor: initialColor
    });

    return {
        selectedColor: picker.selectedColor,
        destroy: picker.destroy
    };
};

//////////////////////////////////////////////////////////////////////////////////////// NOTE LINKS
// Quill 2 stores a scheme-less link like "web.de" verbatim, which the browser then resolves
// against the OctoPrint page - a click ends up on http://<octoprint>/web.de instead of the site
// the user meant. See SPOOLMANAGER_UTILS.normalizeLinkUrl for the details.
//
// The patch has to be applied per editor instance rather than once via Quill.register(): several
// OctoPrint plugins ship their own Quill build and all of them assign the same global (seen in
// packed_plugins.js: PrintJobHistory 1.x, ours 2.0.3, stickypad - whichever loads last wins).
// A global registration would end up on an object the note editor never uses. Patching the link
// blot of the instance's own registry sidesteps the collision entirely.
var _patchNoteEditorLinkFormat = function (noteEditor) {
    var linkFormat = noteEditor.scroll.query("link");
    if (linkFormat == null || linkFormat.__spoolManagerPatched === true) {
        return;
    }
    var originalSanitize = linkFormat.sanitize;
    linkFormat.sanitize = function (url) {
        // the original still runs afterwards, so "javascript:" and "data:" stay neutralised to
        // about:blank - this narrows the accepted set, it does not widen it
        return originalSanitize.call(this, SPOOLMANAGER_UTILS.normalizeLinkUrl(url));
    };
    // guards against patching the same class twice when a second editor is created
    linkFormat.__spoolManagerPatched = true;
};

//////////////////////////////////////////////////////////////////////////////////////// NOTE EDITOR
// Returns the raw editor HTML. Quill 2 offers getSemanticHTML(), but that normalises the
// markup, which would change what gets stored in noteHtml - so keep reading .ql-editor
// directly, exactly as before. Assigned on the instance rather than Quill.prototype: the global
// Quill may be replaced by another plugin's build after this file runs (see NOTE LINKS above),
// which would leave the prototype patch on an object our editor never uses.
var _addGetHtmlHelper = function (noteEditor) {
    noteEditor.getHtml = function () {
        return this.container.querySelector(".ql-editor").innerHTML;
    };
};

// Adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10): returns the raw Quill instance
// (plus a getHtml helper) instead of a wrapper, so callers can use the full Quill API.
ComponentFactory.createNoteEditor = function (elementId) {
    var elementSelector = "#" + elementId;
    var noteEditor = new Quill(elementSelector, {
        modules: {
            toolbar: [
                ["bold", "italic", "underline"],
                [{color: []}, {background: []}],
                [{list: "ordered"}, {list: "bullet"}],
                ["link"]
            ]
        },
        theme: "snow"
    });

    _addGetHtmlHelper(noteEditor);
    _patchNoteEditorLinkFormat(noteEditor);

    return noteEditor;
};
