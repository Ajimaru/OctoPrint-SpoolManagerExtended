
// Static factory methods adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10):
// the component creators are stateless, so they live on ComponentFactory itself and the
// constructor only delegates (prevents needless instantiation & coupling). The method
// bodies are our own (extended color palette, translucent swatch, datetimepicker stub).
function ComponentFactory(pluginId) {

    this.pluginId = pluginId

    this.createDateTimePicker = ComponentFactory.createDateTimePicker;
    this.createColorPicker = ComponentFactory.createColorPicker;
    this.createNoteEditor = ComponentFactory.createNoteEditor;
}

////////////////////////////////////////////////////////////////////////////////////////////////// DATETIME - PICKER
// jQuery datetimepicker removal authored by @mdziekon, adopted via mdziekon/OctoPrint-SpoolManager PR #21.
// The dialog uses native date/datetime-local inputs; this only provides the observables
// the edit dialog binds against.
ComponentFactory.createDateTimePicker = function(elementId, showTimePicker){

    var componentViewModel = {
        currentDateTime: ko.observable(),
        isEnabled: ko.observable(true)
    }

    return componentViewModel;
}


///////////////////////////////////////////////////////////////////////////////////// COLOR - PICKER
ComponentFactory.createColorPicker = function(elementId, showTranslucent){

    // Widget Model
    var componentViewModel = {
        selectedColor: ko.observable(),
        // called when the special "translucent" swatch is picked (only present if showTranslucent == true)
        onTranslucentSelected: null
    }

    var elementSelector = "#" + elementId;
    var pickColor = $(elementSelector).pickAColor({
        showSpectrum          : false,
        showSavedColors       : false,
        saveColorsPerElement  : false,
        fadeMenuToggle        : true,
        showAdvanced          : true,
        showBasicColors       : true,
        showHexInput          : true,
        allowBlank            : false,
        basicColors           : $.extend(showTranslucent == true ? { translucent: 'ffffff' } : {}, {
              white     : 'ffffff',
              black     : '000000',
              red       : 'ff0000',
              green     : '008000',
              blue      : '0000ff',
              yellow    : 'ffff00',
              orange    : 'ffa500',
              purple    : '800080',
              gray      : '808080',
              darkgray  : 'A9A9A9',
              lightgray : 'D3D3D3',
              violet    : 'EE82EE',
              pink      : 'FFC0CB',
              brown     : 'A52A2A',
              burlyWood : 'DEB887',
              cyan      : '00FFFF',
              magenta   : 'FF00FF',
              lime      : '00FF00',
              navy      : '000080',
              teal      : '008080',
              olive     : '808000',
              maroon    : '800000',
              gold      : 'FFD700',
              silver    : 'C0C0C0',
              bronze    : 'CD7F32',
              beige     : 'F5F5DC',
              coral     : 'FF7F50',
              turquoise : '40E0D0',
              indigo    : '4B0082',
              khaki     : 'F0E68C',
              salmon    : 'FA8072',
              lavender  : 'E6E6FA',
              mint      : '3EB489'
            })
    });

    // set while handling a click on the "translucent" swatch, so the
    // change-handler below can ignore the bogus hex pick-a-color derives
    // from the checkerboard swatch (its rendered background-color is
    // transparent, which tinycolor turns into #000000 -> black)
    var handlingTranslucentClick = false;
    if (showTranslucent == true){
        var $translucentLink = $(elementSelector).parent().find(".color-link.translucent");
        // the swatch carries no real color: it only toggles the "transparent"
        // semantic (isTransparent-flag) without setting a base hex color.
        // The flag is armed already on mousedown/touchstart so it is set BEFORE
        // pick-a-color's own click-based change-handler (which would otherwise
        // write the bogus black hex derived from the checkerboard swatch).
        $translucentLink.on("mousedown touchstart", function(){
            handlingTranslucentClick = true;
        });
        $translucentLink.on("click", function(){
            if (componentViewModel.onTranslucentSelected != null){
                componentViewModel.onTranslucentSelected();
            }
            // reset once this click (and pick-a-color's bogus change) is done
            setTimeout(function(){ handlingTranslucentClick = false; }, 0);
        });
    }

    // sync: jquery -> observable
    pickColor.on("change", function () {
        if (handlingTranslucentClick == true){
            // ignore the black hex derived from the checkerboard swatch
            return;
        }
        var newColor = ""+$(this).val();
        if (newColor.startsWith("#") == false){
            newColor = "#" + $(this).val();
        }
        componentViewModel.selectedColor(newColor);
    });

    // sync: observable -> jquery SELCETED_OPTIONS
    componentViewModel.selectedColor.subscribe(function(newColor){
        // check if new color already selected
        currentColor = "#" + pickColor.val();
        if (currentColor != newColor){
            newColorCode = newColor.substr(1);
            pickColor.setColor(newColorCode);
        }

    });


    return componentViewModel;
}


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
var _patchNoteEditorLinkFormat = function(noteEditor){
    var linkFormat = noteEditor.scroll.query('link');
    if (linkFormat == null || linkFormat.__spoolManagerPatched === true){
        return;
    }
    var originalSanitize = linkFormat.sanitize;
    linkFormat.sanitize = function(url){
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
var _addGetHtmlHelper = function(noteEditor){
    noteEditor.getHtml = function() {
        return this.container.querySelector('.ql-editor').innerHTML;
    };
};

// Adopted from mdziekon/OctoPrint-SpoolManager PR #11 (GH-10): returns the raw Quill instance
// (plus a getHtml helper) instead of a wrapper, so callers can use the full Quill API.
ComponentFactory.createNoteEditor = function(elementId){

    var elementSelector = "#" + elementId;
    var noteEditor = new Quill(elementSelector, {
        modules: {
            toolbar: [
                ['bold', 'italic', 'underline'],
                [{ 'color': [] }, { 'background': [] }],
                [{ 'list': 'ordered' }, { 'list': 'bullet' }],
                ['link']
            ]
        },
        theme: 'snow'
    });

    _addGetHtmlHelper(noteEditor);
    _patchNoteEditorLinkFormat(noteEditor);

    return noteEditor;
}
