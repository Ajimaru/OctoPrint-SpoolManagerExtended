# coding=utf-8

# Tests for the OctoScale address normalization. The whole OctoScale HTTP layer had no test
# coverage at all; this is the one part of it that is pure and therefore trivially testable.
#
# Run with:  python3 octoprint_SpoolManager/test/test_OctoScaleUrl.py
# (or `pytest --import-mode=importlib`)

import importlib.util
import os
import sys
import types
import unittest

# Loaded by path rather than by package import: octoprint_SpoolManager/__init__.py pulls in flask
# and OctoPrint, which are not available in a bare test environment. Same harness as
# test_OpenPrintTag.py.
_COMMON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"
)

_PACKAGE_NAME = "spoolmanager_test_common_pkg"
if _PACKAGE_NAME not in sys.modules:
    _package = types.ModuleType(_PACKAGE_NAME)
    _package.__path__ = [_COMMON_DIR]
    sys.modules[_PACKAGE_NAME] = _package


def _loadModule(moduleName):
    modulePath = os.path.join(_COMMON_DIR, moduleName + ".py")
    qualifiedName = _PACKAGE_NAME + "." + moduleName
    spec = importlib.util.spec_from_file_location(qualifiedName, modulePath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualifiedName] = module
    spec.loader.exec_module(module)
    return module


OctoScaleUrl = _loadModule("OctoScaleUrl")


class TestNormalizeOctoScaleUrl(unittest.TestCase):
    def test_bare_ip_gets_http_scheme(self):
        # what people actually type into the settings field
        self.assertEqual(
            "http://192.0.2.20",
            OctoScaleUrl.normalizeOctoScaleUrl("192.0.2.20"),
        )

    def test_hostname_gets_http_scheme(self):
        self.assertEqual(
            "http://octoscale.local",
            OctoScaleUrl.normalizeOctoScaleUrl("octoscale.local"),
        )

    def test_existing_http_scheme_is_kept(self):
        self.assertEqual(
            "http://192.0.2.20",
            OctoScaleUrl.normalizeOctoScaleUrl("http://192.0.2.20"),
        )

    def test_https_scheme_is_kept(self):
        # not what the device serves, but a user-supplied https:// must not be rewritten
        self.assertEqual(
            "https://192.0.2.20",
            OctoScaleUrl.normalizeOctoScaleUrl("https://192.0.2.20"),
        )

    def test_trailing_slash_is_removed(self):
        # paths are appended verbatim ("/weight"), so a trailing slash would double it up
        self.assertEqual(
            "http://192.0.2.20",
            OctoScaleUrl.normalizeOctoScaleUrl("http://192.0.2.20/"),
        )

    def test_multiple_trailing_slashes_are_removed(self):
        self.assertEqual(
            "http://192.0.2.20",
            OctoScaleUrl.normalizeOctoScaleUrl("192.0.2.20///"),
        )

    def test_surrounding_whitespace_is_stripped(self):
        # copy/paste from a label or a chat message brings this along
        self.assertEqual(
            "http://192.0.2.20",
            OctoScaleUrl.normalizeOctoScaleUrl("  192.0.2.20  "),
        )

    def test_port_is_kept(self):
        self.assertEqual(
            "http://192.0.2.20:8080",
            OctoScaleUrl.normalizeOctoScaleUrl("192.0.2.20:8080"),
        )

    def test_none_stays_none(self):
        self.assertIsNone(OctoScaleUrl.normalizeOctoScaleUrl(None))

    def test_empty_string_becomes_none(self):
        # "not configured" must be distinguishable from a usable address
        self.assertIsNone(OctoScaleUrl.normalizeOctoScaleUrl(""))

    def test_whitespace_only_becomes_none(self):
        self.assertIsNone(OctoScaleUrl.normalizeOctoScaleUrl("   "))

    def test_slash_only_becomes_none(self):
        # strips to empty - must not turn into the useless "http://"
        self.assertIsNone(OctoScaleUrl.normalizeOctoScaleUrl("/"))


if __name__ == "__main__":
    unittest.main()
