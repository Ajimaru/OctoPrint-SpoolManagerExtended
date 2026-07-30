// ESLint flat config for the SpoolManager frontend.
//
// The frontend is classic browser script code (no bundler, no modules) loaded by
// OctoPrint, so everything runs in the global scope and relies on globals that
// OctoPrint and its bundled libraries provide.
//
// Only correctness rules are enabled for now; formatting is intentionally left
// alone so linting produces no style churn.

"use strict";

const browserGlobals = {
    // --- browser ---
    window: "readonly",
    document: "readonly",
    console: "readonly",
    navigator: "readonly",
    location: "readonly",
    history: "readonly",
    localStorage: "readonly",
    sessionStorage: "readonly",
    setTimeout: "readonly",
    clearTimeout: "readonly",
    setInterval: "readonly",
    clearInterval: "readonly",
    alert: "readonly",
    confirm: "readonly",
    prompt: "readonly",
    FormData: "readonly",
    FileReader: "readonly",
    Blob: "readonly",
    File: "readonly",
    URL: "readonly",
    URLSearchParams: "readonly",
    XMLHttpRequest: "readonly",
    fetch: "readonly",
    Image: "readonly",
    Event: "readonly",
    CustomEvent: "readonly",
    MutationObserver: "readonly",
    AbortController: "readonly",
    atob: "readonly",
    btoa: "readonly",
    requestAnimationFrame: "readonly",

    // --- jQuery / underscore ---
    $: "readonly",
    jQuery: "readonly",
    _: "readonly",

    // --- OctoPrint core ---
    OctoPrint: "readonly",
    OCTOPRINT_VIEWMODELS: "writable",
    ADDITIONAL_VIEWMODELS: "writable",
    gettext: "readonly",
    ko: "readonly",
    PNotify: "readonly",
    moment: "readonly",
    sprintf: "readonly",
    bootbox: "readonly",
    showMessageDialog: "readonly",
    showConfirmationDialog: "readonly",
    showAlertDialog: "readonly",

    // --- bundled third-party libraries ---
    Quill: "readonly",
    tinycolor: "readonly",
    Modernizr: "readonly",
    Node: "readonly",

    // --- OctoPrint template-injected constants ---
    BASEURL: "readonly",
    API_BASEURL: "readonly",
    PLUGIN_BASEURL: "readonly",
    UI_API_KEY: "readonly",

    // --- SpoolManager's own cross-file globals ---
    // The frontend has no module system. These four namespaces are assigned
    // without a declaration keyword (implicit globals) in common/, so ESLint
    // cannot infer them; the constructors in static/js/*.js are declared with
    // `function`/`var` and are picked up automatically.
    SPOOLMANAGER_CONSTANTS: "writable",
    SPOOLMANAGER_UTILS: "writable",
    SPOOLMANAGER_DIALOGS: "writable",
    SPOOLMANAGER_COLOR_PICKER: "writable",

    // Constructors/helpers declared in one file and used from the others.
    // `no-redeclare` is configured with builtinGlobals:false so the declaring
    // file does not report a conflict against these entries.
    ComponentFactory: "writable",
    TableItemHelper: "writable",
    SpoolItem: "writable",
    SpoolManagerAPIClient: "writable",
    SpoolManagerEditSpoolDialog: "writable",
    SpoolManagerAddSpoolWizard: "writable",
    SpoolManagerImportDialog: "writable",
    SpoolManagerOctoScaleWeighing: "writable",
    SpoolManagerOctoScaleTagWriter: "writable",
    SpoolSelectionTableComp: "writable",
    DatabaseConnectionProblemDialog: "writable",
    ResetSettingsUtilV3: "writable",
};

module.exports = [
    {
        ignores: [
            // vendored libraries - not our code
            "octoprint_SpoolManager/static/js/quill.min.js",
            "octoprint_SpoolManager/static/js/tinycolor.min.js",
            "3rdPartySoftware/**",
            "node_modules/**",
            "build/**",
            "dist/**",
        ],
    },
    {
        files: ["octoprint_SpoolManager/static/js/**/*.js"],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: "script",
            globals: browserGlobals,
        },
        linterOptions: {
            reportUnusedDisableDirectives: true,
        },
        rules: {
            // correctness only - no stylistic rules until the code base is formatted
            "no-undef": "error",
            // `args: none` because knockout/OctoPrint callbacks have fixed
            // signatures. Top-level constructors are consumed from other files
            // through the global scope, which ESLint cannot see per-file.
            "no-unused-vars": [
                "error",
                {
                    args: "none",
                    varsIgnorePattern: "^(_|SpoolManager|SpoolSelection|SpoolItem|TableItemHelper|ComponentFactory|ResetSettingsUtilV3|DatabaseConnectionProblemDialog)",
                    caughtErrors: "none",
                },
            ],
            "no-redeclare": ["error", { builtinGlobals: false }],
            "no-dupe-keys": "error",
            "no-dupe-args": "error",
            "no-duplicate-case": "error",
            "no-unreachable": "error",
            "no-cond-assign": "error",
            "no-constant-condition": ["error", { checkLoops: false }],
            "no-empty": ["error", { allowEmptyCatch: true }],
            "no-extra-boolean-cast": "error",
            "no-func-assign": "error",
            "no-irregular-whitespace": "error",
            "no-sparse-arrays": "error",
            "use-isnan": "error",
            "valid-typeof": "error",
            "no-self-assign": "error",
            "no-fallthrough": "error",
        },
    },
];
