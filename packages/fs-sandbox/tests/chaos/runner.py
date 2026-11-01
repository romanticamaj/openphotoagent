"""Subprocess entry for chaos tests: python runner.py <spec.json> apply|undo|recover"""

import json
import sys

from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox

spec = json.load(open(sys.argv[1]))
cmd = sys.argv[2]
ex = Executor(Sandbox([RootConfig(**r) for r in spec["roots"]]), RealFs(), Journal(spec["journal"]))
if cmd == "apply":
    rep = ex.apply(spec["plan_id"], [Action(**a) for a in spec["actions"]], move_confirmed=True)
    print(json.dumps({"status": rep.status, "results": [r.status for r in rep.results]}))
elif cmd == "undo":
    print(json.dumps([r.status for r in ex.undo(spec["plan_id"])]))
else:
    print(json.dumps(ex.recover()))
