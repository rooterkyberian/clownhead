from importlib.metadata import version

import clownhead


def test_dunder_version_matches_the_packaged_version():
    assert clownhead.__version__ == version("clownhead")
