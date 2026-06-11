# Copyright 2026 dominic.sloanmurphy@canonical.com
# See LICENSE file for licensing details.
#
# To learn more about testing, see https://documentation.ubuntu.com/ops/latest/explanation/testing/

"""Lustre charm unit tests."""

import pytest
from charm import LustreCharm
from ops import testing


@pytest.fixture(scope="function")
def ctx() -> testing.Context[LustreCharm]:
    """Mock charm context."""
    return testing.Context(LustreCharm)


class TestLustreCharm:
    """Unit tests for the Lustre charmed operator."""

    # --- Install ---

    def test_install_success(self, ctx, mocker):
        """Test the install event handler with successful execution."""
        mocker.patch("charm.apt")
        mocker.patch("charm.lustre_fs.init")
        mocker.patch(
            "charm.platform.freedesktop_os_release", return_value={"VERSION_CODENAME": "noble"}
        )

        out = ctx.run(ctx.on.install(), testing.State())

        assert out.unit_status == testing.MaintenanceStatus("Preparing to start Lustre services")
