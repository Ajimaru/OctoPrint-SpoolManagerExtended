function SpoolManagerExtendedSpoolSelectionTableComp() {
    let self = this;
    //////////////////////////////////////////////////////////////////// browser storage

    //////////////////////////////////////////////////////////////////// public functions
    self.registerSpoolSelectionTableComp = function () {
        var spoolSelectionTableCompHTMLTemplate = $("#spmx-spm-select-spool-table").html();
        ko.components.register("spmx-select-spool-table", {
            viewModel: self._viewModelFunction,
            template: spoolSelectionTableCompHTMLTemplate
        });
    };

    //////////////////////////////////////////////////////////////////// private functions
    self._viewModelFunction = function (params) {
        let self = this;

        ////////////////////////////////////////////////////////////////////// public field/functions variables
        self.allSpools = params.allSpoolsKOArray;
        self.allSpools.subscribe(function (neValue) {
            self._executeFilter();
        });
        self.allMaterials = params.allMaterialsKOArray;
        self.allVendors = params.allVendorsKOArray;
        self.allColors = params.allColorsKOArray;
        // Optional: the template selection dialog lists template spools only, where a
        // grand total of all spools in the database would be misleading. Guard against
        // a missing param instead of letting the shared template call undefined().
        self.databaseItemCount = ko.isObservable(params.databaseItemCountKO)
            ? params.databaseItemCountKO
            : null;

        // Optional: only the sidebar selector loads its data lazily and can be "busy"
        // (Attribution @mdziekon, PR #42). The template spool dialog passes no flag, so
        // fall back to a constant-false observable, because unlike databaseItemCount the
        // template dereferences this one unconditionally.
        self.isLoadingSpoolsSelectorData = ko.isObservable(
            params.isLoadingSpoolsSelectorData
        )
            ? params.isLoadingSpoolsSelectorData
            : ko.observable(false);

        self.selectSpoolFunction = params.selectSpoolFunction;

        ////////////////////////////////////////////////////////////////////// internal field variables

        self.totalShown = ko.observable(1);
        // - sorting
        self.currentSortField = ko.observable(); // displayName, lastUse
        self.currentSortOder = ko.observable("ascending"); // or ascending

        // - filtering
        self.filterSelectionQuery = ko.observable();
        self.clearFilterSelectionQuery = function () {
            self.filterSelectionQuery("");
        };
        self.filterSelectionQuery.subscribe(function (filterQuery) {
            self._executeFilter();
        });
        self.hideEmptySpools = ko.observable(true);
        self.hideInActiveSpools = ko.observable(true);

        // - Filtering - Material
        self.selectedMaterialsForFilter = ko.observableArray();
        self.showAllMaterialsForFilter = SPOOLMANAGER_UTILS.buildShowAllForFilterKo(
            self.allMaterials,
            self.selectedMaterialsForFilter
        );
        // - Filtering - Vendor
        self.selectedVendorsForFilter = ko.observableArray();
        self.showAllVendorsForFilter = SPOOLMANAGER_UTILS.buildShowAllForFilterKo(
            self.allVendors,
            self.selectedVendorsForFilter
        );
        // - Filtering - Color
        self.selectedColorsForFilter = ko.observableArray();
        self.showAllColorsForFilter = SPOOLMANAGER_UTILS.buildShowAllForFilterKo(
            self.allColors,
            self.selectedColorsForFilter,
            function (colorItem) {
                return colorItem.colorId;
            }
        );
        // Suppresses the selected*ForFilter subscriptions' _executeFilter()/storage-write
        // while the shared catalog arrays (allMaterials/allVendors/allColors, owned by
        // SpoolManagerExtendedTableItemHelper) grow and this component re-selects "all" into its own selection.
        // This component has no updateCatalogs() of its own - it observes the same catalog
        // KO arrays SpoolManagerExtendedTableItemHelper.updateCatalogs() writes to (see the allCatalogsKO
        // subscription below) - so it needs its own guard, mirroring SpoolManagerExtendedTableItemHelper's
        // isUpdatingCatalogs. Adopted from mdziekon PR #15, extended for this component.
        self.isUpdatingCatalogs = false;

        //////////////////////////////////////////////////////////////////// browser storage
        // var storageKeyPrefix = "spoolmanager.filtersorter." + filterSorterId + ".";
        // All SpoolSelectionTableComponents use the same storage
        var storageKeyPrefix = "spoolmanager.filtersorter.";

        self._loadFilterSelectionsFromBrowserStorage = function () {
            if (!Modernizr.localstorage) {
                // damn, no browser storage!!!
                return false;
            }

            if (localStorage[storageKeyPrefix + "hideEmptySpools"] != null) {
                self.hideEmptySpools(
                    localStorage[storageKeyPrefix + "hideEmptySpools"] == "false"
                        ? false
                        : true
                );
            }
            if (localStorage[storageKeyPrefix + "hideInActiveSpools"] != null) {
                self.hideInActiveSpools(
                    localStorage[storageKeyPrefix + "hideInActiveSpools"] == "false"
                        ? false
                        : true
                );
            }
        };

        self._storeFilterSelectionsToBrowserStorage = function () {
            if (!Modernizr.localstorage) {
                // damn, no browser storage!!!
                return false;
            }
            if (self.hideEmptySpools() != null) {
                localStorage[storageKeyPrefix + "hideEmptySpools"] =
                    self.hideEmptySpools();
            }
            if (self.hideInActiveSpools() != null) {
                localStorage[storageKeyPrefix + "hideInActiveSpools"] =
                    self.hideInActiveSpools();
            }
        };

        self._stringToArray = function (stringValues) {
            var result = stringValues.split("^");
            return result;
        };

        self._arrayToString = function (arrayValues) {
            var result = "";
            arrayValues.forEach(function (value) {
                result += value + "^";
            });
            return result;
        };

        // initial loading from browser storage
        self._loadFilterSelectionsFromBrowserStorage();

        ///////////////////////////////////////////////////////////////////// subscribe listeners
        self.hideEmptySpools.subscribe(function (newValues) {
            self._executeFilter();
            self._storeFilterSelectionsToBrowserStorage();
        });
        self.hideInActiveSpools.subscribe(function (newValues) {
            self._executeFilter();
            self._storeFilterSelectionsToBrowserStorage();
        });
        // showAll*ForFilter is now a computed derived from selected*ForFilter (see its
        // declaration above), so these subscriptions no longer need to toggle it themselves.
        // isUpdatingCatalogs skips the re-filter/storage-write triggered by the catalog-growth
        // subscriptions below re-selecting "all" - that data is already current.
        // Adopted from mdziekon PR #15.
        self.selectedMaterialsForFilter.subscribe(function (newValues) {
            if (self.isUpdatingCatalogs) {
                return;
            }
            self._executeFilter();
            self._storeFilterSelectionsToBrowserStorage();
        });
        self.selectedVendorsForFilter.subscribe(function (newValues) {
            if (self.isUpdatingCatalogs) {
                return;
            }
            self._executeFilter();
            self._storeFilterSelectionsToBrowserStorage();
        });
        self.selectedColorsForFilter.subscribe(function (newValues) {
            if (self.isUpdatingCatalogs) {
                return;
            }
            self._executeFilter();
            self._storeFilterSelectionsToBrowserStorage();
        });

        // This component doesn't own allMaterials/allVendors/allColors - it receives the
        // same KO arrays SpoolManagerExtendedTableItemHelper.updateCatalogs() populates (params.all*KOArray),
        // but keeps its own independent selected*ForFilter. So a catalog refresh (e.g. a
        // newly added filament color) must be mirrored into THIS component's selection
        // separately, or the new entry stays silently excluded while "select all" is active.
        // Extends mdziekon PR #15's updateCatalogs() fix to this component, which has no
        // updateCatalogs() of its own to hook into.
        var reselectAllForGrownCatalog = function (allKo, selectedKo, showAllKo, idMapper) {
            self.isUpdatingCatalogs = true;
            try {
                if (showAllKo()) {
                    SPOOLMANAGER_UTILS.selectAllIntoFilter(allKo, selectedKo, idMapper);
                }
            } finally {
                self.isUpdatingCatalogs = false;
            }
        };
        self.allMaterials.subscribe(function () {
            reselectAllForGrownCatalog(
                self.allMaterials,
                self.selectedMaterialsForFilter,
                self.showAllMaterialsForFilter
            );
        });
        self.allVendors.subscribe(function () {
            reselectAllForGrownCatalog(
                self.allVendors,
                self.selectedVendorsForFilter,
                self.showAllVendorsForFilter
            );
        });
        self.allColors.subscribe(function () {
            reselectAllForGrownCatalog(
                self.allColors,
                self.selectedColorsForFilter,
                self.showAllColorsForFilter,
                function (colorItem) {
                    return colorItem.colorId;
                }
            );
        });

        // Parse format for the "DD.MM.YYYY HH:mm" date fields (24h). Adopted from mdziekon PR #23;
        // replaces the previous "DD.MM.YYYY hh:mm" (12h) which mis-sorted afternoon timestamps.
        const PARSE_FORMAT_DATETIME = SPOOLMANAGER_CONSTANTS.DATES.PARSE_FORMATS.DATETIME;

        // Factory for the shared lastUse/firstUse date comparator (both branches were identical).
        // fieldAccessor(spool) must return the date-string observable value (or null).
        var dateSortCallback = function (fieldAccessor, sortOrientation) {
            return function (a, b) {
                var sortResult;
                var valueA = fieldAccessor(a) != null ? fieldAccessor(a) : "";
                var valueB = fieldAccessor(b) != null ? fieldAccessor(b) : "";
                if (valueA == valueB) {
                    sortResult = b.databaseId() - a.databaseId();
                } else if (valueA == "") {
                    sortResult = 1;
                } else if (valueB == "") {
                    sortResult = -1;
                } else {
                    var momA = moment(valueA, PARSE_FORMAT_DATETIME);
                    var momB = moment(valueB, PARSE_FORMAT_DATETIME);
                    sortResult = momA > momB ? -1 : 1;
                }
                return sortResult * sortOrientation;
            };
        };

        //  - do sorting
        self.sortSpoolArray = function (sortField, requestedSortOrder) {
            var sorted = self.allSpools();

            if (requestedSortOrder) {
                self.currentSortOder(
                    requestedSortOrder == "descending" ? "ascending" : "descending"
                );
            }

            var sortOrientation = 1;
            if (self.currentSortOder() == "descending") {
                self.currentSortOder("ascending");
                sortOrientation = -1;
            } else {
                self.currentSortOder("descending");
            }

            if (sortField === "displayName") {
                sorted.sort(function (a, b) {
                    var sortResult =
                        b
                            .displayName()
                            .toLowerCase()
                            .localeCompare(a.displayName().toLowerCase()) *
                        sortOrientation;
                    return sortResult;
                });
            } else if (sortField === "material") {
                sorted.sort(function sortDesc(a, b) {
                    var valueA = a.material() != null ? a.material().toLowerCase() : "";
                    var valueB = b.material() != null ? b.material().toLowerCase() : "";
                    var sortResult = valueB.localeCompare(valueA) * sortOrientation;

                    return sortResult;
                });
            } else if (sortField === "lastUse") {
                sorted.sort(
                    dateSortCallback(function (spool) {
                        return spool.lastUse();
                    }, sortOrientation)
                );
            } else if (sortField === "firstUse") {
                sorted.sort(
                    dateSortCallback(function (spool) {
                        return spool.firstUse();
                    }, sortOrientation)
                );
            } else if (sortField === "remaining") {
                sorted.sort(function sortDesc(a, b) {
                    var valueA = a.remainingWeight() != null ? a.remainingWeight() : 0;
                    var valueB = b.remainingWeight() != null ? b.remainingWeight() : 0;
                    var sortResult = valueB - valueA;

                    sortResult = sortResult * sortOrientation;
                    return sortResult;
                });
            }
            self.allSpools(sorted);
            self.currentSortField(sortField);
        };

        self.buildFilterLabel = function (filterLabelName) {
            if ("color" == filterLabelName) {
                return SPOOLMANAGER_UTILS.buildFilterSelectionsCounter(
                    self.allColors().map(function (colorItem) {
                        return colorItem.colorId;
                    }),
                    self.selectedColorsForFilter()
                );
            }
            if ("material" == filterLabelName) {
                return SPOOLMANAGER_UTILS.buildFilterSelectionsCounter(
                    self.allMaterials(),
                    self.selectedMaterialsForFilter()
                );
            }
            if ("vendor" == filterLabelName) {
                return SPOOLMANAGER_UTILS.buildFilterSelectionsCounter(
                    self.allVendors(),
                    self.selectedVendorsForFilter()
                );
            }

            return "not defined:" + filterLabelName;
        };

        // doFilterSelectAll removed: showAll*ForFilter is a two-way computed now, so the
        // "select/deselect all" checkbox drives the selection directly via its write().
        // Adopted from mdziekon PR #15.

        // execute the filter
        self._executeFilter = function () {
            var filterQuery =
                self.filterSelectionQuery == null || self.filterSelectionQuery() == null
                    ? ""
                    : self.filterSelectionQuery();
            filterQuery = filterQuery.toLowerCase();
            var totalShownCount = 0;
            //console.error(self.allSpoolsKOArray().length)
            for (let spool of self.allSpools()) {
                var spoolProperties =
                    spool.material() +
                    " " +
                    spool.vendor() +
                    " " +
                    spool.displayName() +
                    " " +
                    spool.colorName();

                if (spoolProperties.toLowerCase().indexOf(filterQuery) > -1) {
                    spool.isFilteredForSelection(false);
                } else {
                    spool.isFilteredForSelection(true);
                }
                if (self.hideEmptySpools() == true) {
                    var isEmpty =
                        spool.remainingWeight == null || spool.remainingWeight() <= 0
                            ? true
                            : false;
                    if (isEmpty) {
                        spool.isFilteredForSelection(true);
                    }
                }
                if (self.hideInActiveSpools() == true && spool.isActive() == false) {
                    spool.isFilteredForSelection(true);
                }

                // Filter against catalogs,  if not already filtered
                if (spool.isFilteredForSelection() == false) {
                    // Material
                    if (
                        self.allMaterials().length !=
                        self.selectedMaterialsForFilter().length
                    ) {
                        var spoolMaterial =
                            spool.material != null && spool.material() != null
                                ? spool.material()
                                : "";
                        if (
                            self.selectedMaterialsForFilter().includes(spoolMaterial) ==
                            false
                        ) {
                            spool.isFilteredForSelection(true);
                        }
                    }
                    if (spool.isFilteredForSelection() == false) {
                        // Vendor
                        if (
                            self.allVendors().length !=
                            self.selectedVendorsForFilter().length
                        ) {
                            var spoolVendor =
                                spool.vendor != null && spool.vendor() != null
                                    ? spool.vendor()
                                    : "";
                            if (
                                self.selectedVendorsForFilter().includes(spoolVendor) ==
                                false
                            ) {
                                spool.isFilteredForSelection(true);
                            }
                        }
                        if (spool.isFilteredForSelection() == false) {
                            // Color
                            if (
                                self.allColors().length !=
                                self.selectedColorsForFilter().length
                            ) {
                                var spoolColorCode =
                                    spool.color != null && spool.color() != null
                                        ? spool.color()
                                        : "";
                                var spoolColorName =
                                    spool.colorName != null && spool.colorName() != null
                                        ? spool.colorName()
                                        : "";
                                var colorId = spoolColorCode + ";" + spoolColorName;
                                if (
                                    self.selectedColorsForFilter().includes(colorId) ==
                                    false
                                ) {
                                    spool.isFilteredForSelection(true);
                                }
                            }
                        }
                    }
                }
                if (spool.isFilteredForSelection() == false) {
                    totalShownCount += 1;
                }
                // });
            }
            self.totalShown(totalShownCount);
        };
    };
}
