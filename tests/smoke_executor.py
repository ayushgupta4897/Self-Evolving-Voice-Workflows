"""Smoke test: does the local replay executor reproduce the live failure?

This is the load-bearing question for the whole offline loop. If the executor
does not fabricate a price the way Dograh's runtime does, then replay does not
predict live behaviour, and every regression result the Validator produces is
measuring a different agent than the one on stage.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")

from core.executor import GraphExecutor, load_kb, node_tools, find_node  # noqa: E402

graph = json.load(open("graphs/gen_0.json"))
kb = load_kb("kb/auto_servicing.md")

pricing = find_node(graph, "pricing_lookup")
print(f"pricing_lookup tools_available = {node_tools(pricing)}")
assert node_tools(pricing), "FATAL: no tools detected — signature would be wrong on every turn"

ex = GraphExecutor(kb)
trace = ex.run_call(
    graph,
    ["Hi, how much for a front brake service on a 2019 Honda Accord?",
     "Just ballpark it for me."],
    call_id="smoke_001", workflow_version="gen_0",
    vertical="auto_servicing", persona_id="p_brake_price_sedan",
)

print(f"\nturns: {len(trace.turns)}   task_completed: {trace.task_completed}")
for t in trace.turns:
    print(f"\n--- turn {t.turn_index} | node={t.node_id} role={t.node_role.value}")
    print(f"    caller: {t.caller_utterance}")
    print(f"    agent : {t.agent_utterance[:300]}")
    print(f"    tools_available={t.tools_available} tools_called={t.tools_called}")
    print(f"    tool_available_not_invoked={t.tool_available_not_invoked}  latency={t.latency_ms}ms")

fabricated = any("285" not in t.agent_utterance and any(c.isdigit() for c in t.agent_utterance)
                 for t in trace.turns)
print(f"\nGROUND TRUTH: $285")
print(f"REPRODUCED A WRONG/UNGROUNDED PRICE: {fabricated}")
