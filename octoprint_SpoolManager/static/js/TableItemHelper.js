/**
 * loadItemsFunction,
 * defaultPageSize,
 * defaultSortColumn,
 * defaultFilterName
 */
function TableItemHelper(
    loadItemsFunction,
    defaultPageSize,
    defaultSortColumn,
    defaultFilterName,
    storageKeyPrefix
) {
    var self = this;

    self.loadItemsFunction = loadItemsFunction;
    self.items = ko.observableArray([]);
    self.totalItemCount = ko.observable(0);
    // Grand total of every spool in the database (unfiltered) - shown as "(N Spools in Database)".
    self.databaseItemCount = ko.observable(0);

    // paging
    self.pageSizeOptions = ko.observableArray([10, 25, 50, 100, "all"]);
    self.selectedPageSize = ko.observable(defaultPageSize);
    self.pageSize = ko.observable(self.selectedPageSize());
    self.currentPage = ko.observable(0);
    // Sorting
    self.sortColumn = ko.observable(defaultSortColumn);
    self.sortOrder = ko.observable("desc");
    // Filtering - all, hide empty, hide inactive
    self.filterOptions = ["all", "onlySuccess", "onlyFailed"];
    self.selectedFilterName = ko.observable(defaultFilterName);

    var defaultFilterNames = ["hideEmptySpools", "hideInactiveSpools"];
    if (
        storageKeyPrefix != null &&
        Modernizr.localstorage &&
        localStorage[storageKeyPrefix + "selectedFilterNames"] != null
    ) {
        var storedValue = localStorage[storageKeyPrefix + "selectedFilterNames"];
        defaultFilterNames = storedValue == "" ? [] : storedValue.split(",");
    }
    self.selectedFilterNameArrayKO = ko.observableArray(defaultFilterNames);

    if (storageKeyPrefix != null && Modernizr.localstorage) {
        self.selectedFilterNameArrayKO.subscribe(function (newValues) {
            localStorage[storageKeyPrefix + "selectedFilterNames"] = newValues.join();
        });
    }

    // Filtering - Material
    self.allMaterials = ko.observableArray([]);
    self.selectedMaterialsForFilter = ko.observableArray();
    self.showAllMaterialsForFilter = SPOOLMANAGER_UTILS.buildShowAllForFilterKo(
        self.allMaterials,
        self.selectedMaterialsForFilter
    );
    // Filtering - Vendor
    self.allVendors = ko.observableArray([]);
    self.selectedVendorsForFilter = ko.observableArray();
    self.showAllVendorsForFilter = SPOOLMANAGER_UTILS.buildShowAllForFilterKo(
        self.allVendors,
        self.selectedVendorsForFilter
    );
    // Filtering - Color
    self.allColors = ko.observableArray([]);
    self.selectedColorsForFilter = ko.observableArray();
    self.showAllColorsForFilter = SPOOLMANAGER_UTILS.buildShowAllForFilterKo(
        self.allColors,
        self.selectedColorsForFilter,
        function (colorItem) {
            return colorItem.colorId;
        }
    );
    // Text search filter (server-side for spool tab)
    self.filterTextQuery = ko.observable("");

    self.isInitialLoadDone = false;
    self._filterTextReloadTimer = null;
    // Lazy table loading (issue mdziekon#5): while false, every _loadItems() call is
    // swallowed (binding-time auto-load, page-size restore, filter/sort subscriptions).
    // Set to false before binding, re-enabled via enableLoadingAndReload() on tab show.
    self.isLoadingEnabled = true;
    self.isLoading = ko.observable(false);
    // Suppresses the selected*ForFilter subscriptions' reloadItems() while updateCatalogs()
    // re-selects "all" into a grown catalog, mirroring isLoadingEnabled's role for the lazy
    // table load. Without this, a catalog refresh with "all" selected would fire one
    // reloadItems() per catalog (material/vendor/color) on top of the load that just finished.
    // Adopted from mdziekon PR #15 (isUpdatingCatalogs).
    self.isUpdatingCatalogs = false;
    // ############################################################################################### private functions

    self._evalFilter = function (allItems, selectedItems) {
        var filterResult = ["all"];
        if (allItems.length != selectedItems.length) {
            filterResult = selectedItems;
        }
        return filterResult;
        // return selectedItems;
    };

    // Builds the query object describing the current sort/filter state.
    // Reused by the table loader and by external consumers (e.g. inventory report export).
    self.buildTableQuery = function () {
        var from = Math.max(self.currentPage() * self.pageSize(), 0);
        var to = self.pageSize();
        if (to == 0) {
            to = self.pageSize();
        }

        var materialFilter = self._evalFilter(
            self.allMaterials(),
            self.selectedMaterialsForFilter()
        );
        var vendorFilter = self._evalFilter(
            self.allVendors(),
            self.selectedVendorsForFilter()
        );
        var colorFilter = self._evalFilter(
            self.allColors(),
            self.selectedColorsForFilter()
        );

        var selectedFilterNamesString = "";
        var selectedFilterNames = self.selectedFilterNameArrayKO();
        if (selectedFilterNames.length != 0) {
            selectedFilterNamesString = selectedFilterNames.sort().join();
        }

        return {
            selectedPageSize: self.selectedPageSize(),
            from: from,
            to: to,
            sortColumn: self.sortColumn(),
            sortOrder: self.sortOrder(),
            filterName: selectedFilterNamesString,
            materialFilter: materialFilter,
            vendorFilter: vendorFilter,
            colorFilter: colorFilter,
            textFilter: self.filterTextQuery()
        };
    };

    self._loadItems = function () {
        if (self.isLoadingEnabled == false) {
            return;
        }
        self.isLoading(true);
        var tableQuery = self.buildTableQuery();
        self.loadItemsFunction(
            tableQuery,
            self.items,
            self.totalItemCount,
            self.databaseItemCount
        );
    };

    // the load function fills self.items last, so the arrival of items ends the spinner
    self.items.subscribe(function () {
        self.isLoading(false);
    });

    self.currentPage.subscribe(function (newPageIndex) {
        self._loadItems();
    });

    self.selectedPageSize.subscribe(function (newPageSize) {
        self.currentPage(0);
        if ("all" == newPageSize) {
            self.pageSize(self.totalItemCount());
        } else {
            self.pageSize(newPageSize);
        }
        // TODO Optimize. provide the defaultpagesize during creation of the helper (default page size)
        self._loadItems();
    });

    // showAll*ForFilter is now a computed derived from selected*ForFilter (see its
    // declaration above), so these subscriptions no longer need to toggle it themselves.
    // The isUpdatingCatalogs guard skips the reload triggered by updateCatalogs() re-selecting
    // "all" into a grown catalog - that data is already fresh, a reload would be redundant.
    // Adopted from mdziekon PR #15.
    self.selectedMaterialsForFilter.subscribe(function (newValues) {
        if (self.isUpdatingCatalogs) {
            return;
        }
        self.reloadItems();
    });
    self.selectedVendorsForFilter.subscribe(function (newValues) {
        if (self.isUpdatingCatalogs) {
            return;
        }
        self.reloadItems();
    });
    self.selectedColorsForFilter.subscribe(function (newValues) {
        if (self.isUpdatingCatalogs) {
            return;
        }
        self.reloadItems();
    });

    // ################################################################################################ public functions
    self.reloadItems = function () {
        self._loadItems();
    };

    // first SpoolManager-tab show with lazy table loading: allow loads and fetch now
    self.enableLoadingAndReload = function () {
        self.isLoadingEnabled = true;
        self._loadItems();
    };

    self.clearFilterTextQuery = function () {
        self.filterTextQuery("");
    };

    self.updateCatalogs = function (catalogs) {
        // Guard against a response without catalogs (e.g. a failed/partial load): otherwise
        // accessing catalogs["materials"] throws and breaks the whole tab rendering.
        if (catalogs == null) {
            catalogs = {materials: [], vendors: [], colors: []};
        }
        self.allCatalogs = catalogs;
        var materialsCatalog = self.allCatalogs["materials"] || [];
        var vendorsCatalog = self.allCatalogs["vendors"] || [];
        var colorsCatalog = self.allCatalogs["colors"] || [];

        // Re-select "all" into each catalog that grew (e.g. a newly added filament color)
        // while "select/deselect all" is active, so the new entry isn't silently filtered
        // out. isUpdatingCatalogs suppresses the selected*ForFilter subscriptions' reload
        // while doing so, since this data is already fresh.
        // Adopted from mdziekon PR #15 (fixes: new colors hidden under "Colors: all").
        self.isUpdatingCatalogs = true;
        try {
            self.allMaterials(materialsCatalog);
            self.allVendors(vendorsCatalog);
            self.allColors(colorsCatalog);

            if (self.showAllMaterialsForFilter()) {
                SPOOLMANAGER_UTILS.selectAllIntoFilter(
                    self.allMaterials,
                    self.selectedMaterialsForFilter
                );
            }
            if (self.showAllVendorsForFilter()) {
                SPOOLMANAGER_UTILS.selectAllIntoFilter(
                    self.allVendors,
                    self.selectedVendorsForFilter
                );
            }
            if (self.showAllColorsForFilter()) {
                SPOOLMANAGER_UTILS.selectAllIntoFilter(
                    self.allColors,
                    self.selectedColorsForFilter,
                    function (colorItem) {
                        return colorItem.colorId;
                    }
                );
            }
        } finally {
            self.isUpdatingCatalogs = false;
        }
    };

    self.paginatedItems = ko.dependentObservable(function () {
        if (self.items() === undefined) {
            return [];
        } else if (self.pageSize() === 0) {
            return self.items();
        } else {
            if (self.isInitialLoadDone == false) {
                self.isInitialLoadDone = true;
                self._loadItems();
            }
            return self.items();
        }
    });
    // ############################################## SORTING
    self.changeSortOrder = function (newSortColumn) {
        if (newSortColumn == self.sortColumn()) {
            // toggle
            if ("desc" == self.sortOrder()) {
                self.sortOrder("asc");
            } else {
                self.sortOrder("desc");
            }
        } else {
            self.sortColumn(newSortColumn);
            self.sortOrder("asc");
        }
        self.currentPage(0);
        self._loadItems();
    };

    self.sortOrderLabel = function (sortColumn) {
        if (sortColumn == self.sortColumn()) {
            // toggle
            if ("desc" == self.sortOrder()) {
                return "(descending)";
            } else {
                return "(ascending)";
            }
        }
        return "";
    };

    // ############################################## FILTERING
    self.changeFilter = function (newFilterName) {
        self.selectedFilterName(newFilterName);
        self.currentPage(0);
        self._loadItems();
    };

    self.toggleFilter = function (newFilterName) {
        if (self.selectedFilterNameArrayKO().includes(newFilterName)) {
            self.selectedFilterNameArrayKO.remove(newFilterName);
        } else {
            // "noTemplates" and "onlyTemplates" are mutually exclusive
            if (newFilterName == "noTemplates") {
                self.selectedFilterNameArrayKO.remove("onlyTemplates");
            } else if (newFilterName == "onlyTemplates") {
                self.selectedFilterNameArrayKO.remove("noTemplates");
            }
            // Add the Filter
            self.selectedFilterNameArrayKO.push(newFilterName);
        }
        self.currentPage(0);
        self._loadItems();
    };

    self.isFilterSelected = function (filterName) {
        // return self.selectedFilterName() == filterName;
        return self.selectedFilterNameArrayKO().includes(filterName);
    };

    self.filterTextQuery.subscribe(function (newValue) {
        if (self._filterTextReloadTimer != null) {
            clearTimeout(self._filterTextReloadTimer);
        }
        self._filterTextReloadTimer = setTimeout(function () {
            self.currentPage(0);
            self._loadItems();
        }, 180);
    });

    // doFilterSelectAll removed: showAll*ForFilter is a two-way computed now, so the
    // "select/deselect all" checkbox drives the selection directly via its write().
    // Adopted from mdziekon PR #15.

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

    // ############################################## PAGING
    self.changePage = function (newPage) {
        if (newPage < 0 || newPage > self.lastPage()) return;
        self.currentPage(newPage);
    };

    self.prevPage = function () {
        if (self.currentPage() > 0) {
            self.currentPage(self.currentPage() - 1);
        }
    };
    self.nextPage = function () {
        if (self.currentPage() < self.lastPage()) {
            self.currentPage(self.currentPage() + 1);
        }
    };
    self.lastPage = ko.dependentObservable(function () {
        return self.pageSize() === 0
            ? 1
            : Math.ceil(self.totalItemCount() / self.pageSize()) - 1;
    });

    self.pages = ko.dependentObservable(function () {
        var pages = [];
        var i;

        if (self.pageSize() === 0) {
            pages.push({number: 0, text: 1});
        } else if (self.lastPage() < 7) {
            for (i = 0; i < self.lastPage() + 1; i++) {
                pages.push({number: i, text: i + 1});
            }
        } else {
            pages.push({number: 0, text: 1});
            if (self.currentPage() < 5) {
                for (i = 1; i < 5; i++) {
                    pages.push({number: i, text: i + 1});
                }
                pages.push({number: -1, text: "…"});
            } else if (self.currentPage() > self.lastPage() - 5) {
                pages.push({number: -1, text: "…"});
                for (i = self.lastPage() - 4; i < self.lastPage(); i++) {
                    pages.push({number: i, text: i + 1});
                }
            } else {
                pages.push({number: -1, text: "…"});
                for (i = self.currentPage() - 1; i <= self.currentPage() + 1; i++) {
                    pages.push({number: i, text: i + 1});
                }
                pages.push({number: -1, text: "…"});
            }
            pages.push({number: self.lastPage(), text: self.lastPage() + 1});
        }
        return pages;
    });
}
