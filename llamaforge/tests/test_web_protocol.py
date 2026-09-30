import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["test_web_protocol.cjs", "test_workspace_ui.cjs"])
def test_browser_protocol_behaviour(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser protocol tests")
    subprocess.run([node, str(Path(__file__).with_name(script))], check=True, capture_output=True, text=True)
