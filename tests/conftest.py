"""Test setup.

`Parameter3D` in the engine imports `ThreeDUrlArtifact` from the standard library, which the engine
always has loaded and this library's dev environment does not install. A stand-in lets the node
files import.
"""

from __future__ import annotations

import importlib.util
import sys
import types

from griptape.artifacts import UrlArtifact

if importlib.util.find_spec("griptape_nodes_library") is None:

    class ThreeDUrlArtifact(UrlArtifact):
        pass

    for name in (
        "griptape_nodes_library",
        "griptape_nodes_library.three_d",
        "griptape_nodes_library.three_d.three_d_artifact",
    ):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["griptape_nodes_library.three_d.three_d_artifact"].ThreeDUrlArtifact = ThreeDUrlArtifact  # type: ignore[attr-defined]
