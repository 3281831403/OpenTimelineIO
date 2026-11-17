# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project

""" Unit tests of functions that convert between file paths and urls. """

import unittest
import os

import opentimelineio as otio

MEDIA_EXAMPLE_PATH_REL = os.path.relpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",  # root
        "docs",
        "_static",
        "OpenTimelineIO@3xDark.png"
    )
)
MEDIA_EXAMPLE_PATH_URL_REL = otio.url_utils.url_from_filepath(
    MEDIA_EXAMPLE_PATH_REL
)
MEDIA_EXAMPLE_PATH_ABS = os.path.abspath(
    MEDIA_EXAMPLE_PATH_REL.replace(
        "3xDark",
        "3xLight"
    )
)
MEDIA_EXAMPLE_PATH_URL_ABS = otio.url_utils.url_from_filepath(
    MEDIA_EXAMPLE_PATH_ABS
)


class TestConversions(unittest.TestCase):
    def test_roundtrip_abs(self):
        self.assertTrue(MEDIA_EXAMPLE_PATH_URL_ABS.startswith("file://"))
        full_path = os.path.abspath(
            otio.url_utils.filepath_from_url(MEDIA_EXAMPLE_PATH_URL_ABS)
        )

        # should have reconstructed it by this point
        self.assertEqual(full_path, MEDIA_EXAMPLE_PATH_ABS)

    def test_roundtrip_rel(self):
        self.assertFalse(MEDIA_EXAMPLE_PATH_URL_REL.startswith("file://"))

        result = otio.url_utils.filepath_from_url(MEDIA_EXAMPLE_PATH_URL_REL)

        # should have reconstructed it by this point
        self.assertEqual(os.path.normpath(result), MEDIA_EXAMPLE_PATH_REL)

    def test_non_file_scheme_passes_through(self):
        # The C++ file_from_url returns None for non-file schemes;
        # the Python wrapper passes the input through unchanged, matching
        # the historical Python behavior of not raising on unrecognized
        # input.
        self.assertEqual(
            otio.url_utils.filepath_from_url("http://example.com/foo"),
            "http://example.com/foo",
        )

    def test_roundtrip_special_characters(self):
        # Characters that are significant in URLs ('#', '%', spaces,
        # non-ASCII) must survive the path -> url -> path round trip.
        for path in (
                "/tmp/a b#c.mov",
                "/tmp/100%.mov",
                "/tmp/100%25.mov",
                "/tmp/ünïcode #2.exr"):
            url = otio.url_utils.url_from_filepath(path)
            self.assertEqual(
                otio.url_utils.filepath_from_url(url),
                path
            )

    def test_file_url_and_plain_path_are_equivalent(self):
        # A file:// URL and the corresponding plain path resolve to
        # the same file.
        self.assertEqual(
            otio.url_utils.filepath_from_url("file:///tmp/dir/thing.mov"),
            "/tmp/dir/thing.mov"
        )
        self.assertEqual(
            otio.url_utils.filepath_from_url("/tmp/dir/thing.mov"),
            "/tmp/dir/thing.mov"
        )


if __name__ == "__main__":
    unittest.main()
