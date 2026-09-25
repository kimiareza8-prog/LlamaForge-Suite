"""Reproducible control-plane microbenchmark; no LLM throughput claims."""
import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from llamaforge.core import agent_tools
from llamaforge.core.agent_tools import AgentRuntime, AgentPermissions
from llamaforge.core.skill_system import SkillRegistry


def measure():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for key, path in {"AGENT_DIR":"agent", "CONNECTORS_PATH":"agent/connectors.json", "SKILLS_DIR":"skills", "BROWSER_PROFILE_DIR":"browser", "DOWNLOADS_DIR":"downloads", "WORKSPACE_DIR":"workspace"}.items():
            setattr(agent_tools, key, root / path)
        rt = AgentRuntime(); perms = AgentPermissions(); reg = SkillRegistry(rt, perms)
        sizes = []; times = []
        for _ in range(25):
            start = time.perf_counter()
            rows = reg.shortlist("Create a meeting tomorrow", ["calendar"], limit=5)
            manifest, names = reg.manifest(rows, rt.tool_definitions(perms))
            times.append((time.perf_counter()-start)*1000); sizes.append(len(names))
        calls = []
        def model(messages, tools):
            calls.append(1)
            return {"content":'{"route":"direct"}' if "stage 0" in str(messages) else "Hello!"}
        list(rt.run([{"role":"user", "content":"Hello!"}], model, perms))
        attachment = {"kind":"text", "name":"notes.txt", "text":"x"*100000}
        start = time.perf_counter()
        ids = [rt.workspace.stage_attachment(attachment)["attachment_id"] for _ in range(20)]
        return {"kind":"control-plane; stub model; not inference", "greeting_model_calls":len(calls),
                "calendar_shortlist_count":statistics.median(sizes), "shortlist_median_ms":statistics.median(times),
                "calendar_manifest_chars":len(manifest), "repeated_attachment_unique_ids":len(set(ids)),
                "staging_20_turns_ms":(time.perf_counter()-start)*1000,
                "inbox_bytes":sum(p.stat().st_size for p in rt.workspace.inbox_root.iterdir())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output")
    parser.add_argument("--repeat", type=int, default=7)
    args = parser.parse_args()
    runs = [measure() for _ in range(max(1, min(25, args.repeat)))]
    combined = {key: statistics.median(r[key] for r in runs) if isinstance(value, (int, float)) else value
                for key, value in runs[0].items()}
    combined["repeats"] = len(runs)
    combined["runs"] = runs
    result = json.dumps(combined, indent=2)
    if args.output: Path(args.output).write_text(result+"\n", encoding="utf-8")
    print(result)
