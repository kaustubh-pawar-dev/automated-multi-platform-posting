"""The test environment must not depend on the developer's shell."""

import os

from app.core.config import Settings


def test_setting_variables_are_cleared_from_the_environment_for_every_test() -> None:
    leaked = [name for name in Settings.model_fields if name.upper() in os.environ]

    assert leaked == []