#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Exceptions used within the charm."""


class LustreError(Exception):
    """Base class for Lustre-related errors."""

    pass


class LustreFilesystemError(LustreError):
    """Raised when a Lustre file system operation fails."""


class LustrePeerError(LustreError):
    """Raised when a Lustre peer relation operation fails."""

    pass
