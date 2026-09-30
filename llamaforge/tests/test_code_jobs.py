import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from llamaforge.core.code_jobs import CodeJobs
from llamaforge.core.agent_tools import AgentPermissions, AgentRuntime
from llamaforge.core.agent_engine import AgentEngine
from llamaforge.core.skill_system import SkillRegistry


class CodeJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.jobs = CodeJobs(Path(self.tmp.name))
        self.job_id = self.jobs.tool({"operation":"new", "name":"test"})["job_id"]

    def call(self, operation, **kwargs):
        return self.jobs.tool({"operation":operation, "job_id":self.job_id, **kwargs})

    def tearDown(self):
        self.jobs.stop_all()

    def test_failure_edit_rerun_and_input(self):
        self.call("write", path="main.py", content="raise ValueError('broken')\n")
        self.call("run")
        result = self.call("wait", wait_seconds=5)
        self.assertEqual(result["status"], "failed")
        self.assertIn("ValueError: broken", result["output"])
        self.call("replace", path="main.py", old_text="raise ValueError('broken')", new_text="print(input(), flush=True)")
        self.assertIn("print(input()", self.call("read", path="main.py")["content"])
        self.call("run")
        self.call("input", content="hello")
        result = self.call("wait", wait_seconds=5)
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["output"].strip(), "hello")
        self.assertEqual(len(self.call("files")["files"]), 1)

    def test_stop_command_and_package_check(self):
        self.call("run", command=[sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(30)"])
        self.assertIn("ready", self.call("wait", wait_seconds=2)["output"])
        self.assertTrue(self.call("stop")["stopped"])
        self.assertEqual(self.call("status")["status"], "stopped")
        self.assertIsNotNone(self.call("check_packages", packages=["pip"])["packages"]["pip"])
        self.call("install", packages=["pip"])
        self.assertEqual(self.call("wait", wait_seconds=15)["status"], "finished")

    def test_path_and_package_validation(self):
        with self.assertRaises(ValueError):
            self.call("write", path="../outside.py", content="x")
        folder = Path(self.tmp.name) / self.job_id
        (folder / "escape").symlink_to(Path(self.tmp.name).parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.call("write", path="escape/outside.py", content="x")
        with self.assertRaises(ValueError):
            self.call("install", packages=["--target=/tmp"])

    def test_timeout_and_restarted_server_state(self):
        self.call("run", command=[sys.executable, "-u", "-c", "import time; time.sleep(30)"], timeout_seconds=1, wait_seconds=0)
        self.assertEqual(self.call("wait", wait_seconds=4)["status"], "timed_out")
        self.call("write", path="main.py", content="print('again')\n")
        folder = Path(self.tmp.name) / self.job_id
        state = json.loads((folder / "state.json").read_text())
        state["status"] = "running"
        (folder / "state.json").write_text(json.dumps(state))
        restarted = CodeJobs(Path(self.tmp.name))
        self.assertEqual(restarted.tool({"operation":"status", "job_id":self.job_id})["status"], "interrupted")

    def test_agent_treats_nonzero_program_exit_as_failure_observation(self):
        wrapped = json.dumps({
            "ok": True,
            "result": {
                "job_id": self.job_id,
                "status": "failed",
                "exit_code": 1,
                "output": "Traceback\nValueError: broken",
            },
        })
        ok, error = AgentEngine._parse_tool_result(wrapped, "code_job")
        self.assertFalse(ok)
        self.assertIn("exit 1", error)
        self.assertIn("ValueError: broken", error)

    def test_agent_keeps_running_program_as_successful_launch(self):
        wrapped = json.dumps({
            "ok": True,
            "result": {"job_id": self.job_id, "status": "running", "exit_code": None},
        })
        ok, error = AgentEngine._parse_tool_result(wrapped, "code_job")
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_permissions_and_discovery(self):
        runtime = AgentRuntime(log=lambda _:None)
        runtime.code_jobs = self.jobs
        disabled = AgentPermissions()
        enabled = AgentPermissions(allow_code_execution=True)
        self.assertNotIn("code_job", [x["function"]["name"] for x in runtime.tool_definitions(disabled)])
        self.assertIn("code_job", [x["function"]["name"] for x in runtime.tool_definitions(enabled)])
        self.assertIn("code", SkillRegistry.hinted_families("Write a Python script and run it"))
        self.assertTrue(next(x for x in SkillRegistry(runtime, enabled).catalog() if x["name"]=="code_job")["available"])
        self.assertFalse(json.loads(runtime.execute("code_job", {"operation":"new"}, disabled))["ok"])
        runtime.set_workspace_scope("remote_example")
        self.assertFalse(json.loads(runtime.execute("code_job", {"operation":"new"}, enabled))["ok"])

    def test_agent_stream_exposes_project_receipt_for_live_window(self):
        runtime = AgentRuntime(log=lambda _:None)
        runtime.code_jobs = self.jobs
        decisions = iter([
            {"action":"tool", "skill":"code_job", "arguments":{"operation":"new", "name":"Visual demo"}},
            {"action":"final", "answer":"Project created."},
        ])
        def model(_messages, _tools):
            return {"content":json.dumps(next(decisions))}
        events = list(AgentEngine(runtime).run(
            [{"role":"user","content":"Write a Python program for a calculator"}], model,
            AgentPermissions(allow_code_execution=True), max_steps=2))
        starts = [x for x in events if x.get("event") == "tool_start" and x.get("tool") == "code_job"]
        receipts = [x["code_job"] for x in events if x.get("event") == "tool_result" and x.get("tool") == "code_job"]
        self.assertEqual(starts[0]["arguments"]["operation"], "new")
        self.assertEqual(receipts[0]["operation"], "new")
        self.assertRegex(receipts[0]["job_id"], r"^job_[0-9a-f]{16}$")


if __name__ == "__main__":
    unittest.main()
