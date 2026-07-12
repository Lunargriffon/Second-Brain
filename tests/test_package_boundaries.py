import importlib

import pytest


@pytest.mark.parametrize(
    "package_name",
    [
        "pkb.sources",
        "pkb.knowledge",
        "pkb.derive",
        "pkb.wiki",
        "pkb.interfaces",
    ],
)
def test_phase_two_package_is_importable(package_name: str) -> None:
    package = importlib.import_module(package_name)

    assert package.__doc__ == (
        "Focused Phase 2 package; public APIs are exported explicitly by modules."
    )
