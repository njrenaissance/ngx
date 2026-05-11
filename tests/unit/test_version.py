"""Validate that the package version is a well-formed semantic version.

This guards against accidentally shipping a malformed version string. The full
SemVer 2.0.0 grammar is at https://semver.org/#backus-naur-form-grammar-for-valid-semver-versions.
"""

import re

from forge import __version__

# Subset of the SemVer 2.0.0 BNF — sufficient for MAJOR.MINOR.PATCH plus optional
# pre-release (-alpha.1) and build metadata (+sha.abc) identifiers.
SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def test_version_is_semver() -> None:
    assert SEMVER_RE.match(__version__), f"forge.__version__ {__version__!r} is not valid SemVer 2.0.0"
