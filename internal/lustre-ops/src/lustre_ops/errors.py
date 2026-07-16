# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details

"""Exceptions raised by the `lustre_ops` package."""


class LustreOpsError(Exception):
    """Base class for errors raised by the `lustre_ops` package."""


class LNetError(LustreOpsError):
    """Raised when an LNet operation fails."""


class RepositoryError(LustreOpsError):
    """Raised when a Lustre package repository operation fails."""
