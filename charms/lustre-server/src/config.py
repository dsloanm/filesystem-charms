#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Lustre charm configuration model."""

import pydantic


class LustreConfig(pydantic.BaseModel):
    """Lustre charm configuration model."""

    lnet_networks: str = pydantic.Field("")
