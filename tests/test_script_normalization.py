#!/usr/bin/env python3

from kdotnet_dump.dumper import Dumper


def test_normalize_script_newlines_crlf_to_lf():
    source = "line1\r\nline2\r\nline3\r\n"
    expected = "line1\nline2\nline3\n"
    assert Dumper._normalize_script_newlines(source) == expected


def test_normalize_script_newlines_cr_to_lf():
    source = "line1\rline2\rline3\r"
    expected = "line1\nline2\nline3\n"
    assert Dumper._normalize_script_newlines(source) == expected


def test_normalize_script_newlines_lf_unchanged():
    source = "line1\nline2\nline3\n"
    assert Dumper._normalize_script_newlines(source) == source
