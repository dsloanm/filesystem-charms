#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Exceptions used within the charm."""


import ops


class LustreError(Exception):
    """Base class for Lustre-related errors."""

    pass


class LustreFilesystemError(LustreError):
    """Raised when a Lustre file system operation fails."""


class LustrePeerError(LustreError):
    """Raised when a Lustre peer relation operation fails."""

    pass


class LustreStateError(LustreError):
    """Raised when a Lustre status check fails, carrying the resulting unit status."""

    def __init__(self, status: ops.StatusBase):
        super().__init__(status.message)
        self.status = status
