"""Shared utilities for v2 agent."""

from __future__ import annotations

import re


def path_stem(path: str) -> str:
    """Extract normalized filename stem for fuzzy path matching.

    Strips extensions, date suffixes, and trailing version numbers so that
    document revisions (e.g., Standards-3.docx vs -4.docx) map to the
    same stem.
    """
    name = path.rsplit("/", 1)[-1].lower()
    name = re.sub(r'\.[^.]+$', '', name)  # strip extension
    # Strip date suffixes FIRST (before version numbers)
    name = re.sub(r'[-_]\d{1,2}[a-z]{3}\d{2,4}$', '', name)  # _22Dec2016
    name = re.sub(r'[-_]\d{4}[a-z]{3}\d{1,2}$', '', name)    # _2017Jun30
    # Strip trailing 1-2 digit version only after a letter (prevents IMG_0685 → IMG)
    name = re.sub(r'(?<=[a-z])[-_]?\d{1,2}$', '', name)
    return name.strip('_- ')
