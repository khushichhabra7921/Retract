import retract
import retract.core


def test_package_imports() -> None:
    assert retract.__doc__
    assert retract.core.__doc__
