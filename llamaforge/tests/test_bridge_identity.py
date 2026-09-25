"""Run the real PHP updater functions in a disposable Bridge installation."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_bridge_update_preserves_identity_config_and_live_code_on_copy_failure(tmp_path):
    php = shutil.which("php")
    if not php:
        pytest.skip("PHP CLI unavailable; Bridge source is unchanged")
    source = Path(__file__).resolve().parents[2] / "web-bridge"
    root = tmp_path / "bridge"
    shutil.copytree(source, root)
    stage = tmp_path / "stage"
    (stage / "data").mkdir(parents=True)
    (stage / "data/config.php").write_text("<?php return ['agent_token'=>'wrong'];")
    (stage / "index.php").write_text("new live code")
    (root / "data/config.php").write_text("<?php return ['owner_key'=>'TEST_OWNER','agent_token'=>'TEST_AGENT','custom'=>'keep'];")
    script = tmp_path / "verify.php"
    script.write_text("<?php\nrequire " + json.dumps(str(root / "lib/bootstrap.php")) + ";\n"
        + "$code=file_get_contents(" + json.dumps(str(root / "bridge-update.php")) + ");\n"
        + "$a=strpos($code,'const AIB_UPDATER_VERSION'); $b=strpos($code, chr(10).'$method =');\n"
        + "eval(substr($code,$a,$b-$a));\n"
        + "$before=file_get_contents(AIB_CONFIG_FILE);$id=aib_update_identity_snapshot();\n"
        + "aib_update_deploy_stage(" + json.dumps(str(stage)) + ");\n"
        + "if(file_get_contents(AIB_CONFIG_FILE)!==$before)exit(10);\n"
        + "aib_update_restore_identity($id,'test-version');$after=aib_update_read_config_file();\n"
        + "if($after['owner_key']!=='TEST_OWNER'||$after['agent_token']!=='TEST_AGENT'||$after['custom']!=='keep')exit(11);\n"
        + "try{aib_update_atomic_copy_file(AIB_ROOT.'/missing',AIB_ROOT.'/index.php');exit(12);}catch(Throwable $e){}\n"
        + "if(file_get_contents(AIB_ROOT.'/index.php')!=='new live code')exit(13);\n")
    subprocess.run([php, str(script)], check=True, capture_output=True, text=True)
