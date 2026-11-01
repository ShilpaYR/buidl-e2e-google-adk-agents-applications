import os
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))
os.environ.setdefault("GOOGLE_API_KEY", "offline-test-key")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

# A fake `gemini` executable: proves the subprocess contract without calling Google.
_tmp = Path(tempfile.mkdtemp(prefix="fake_gemini_"))
FAKE_LOG = _tmp / "calls.log"
fake = _tmp / "gemini"
fake.write_text(f"""#!/usr/bin/env python3
import json, os, sys, time
with open({str(FAKE_LOG)!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
time.sleep(1.0)
prompt = sys.argv[sys.argv.index("-p") + 1]
print(json.dumps({{"session_id": "fake", "response": "Gemini CLI analysed " + os.path.basename(os.getcwd()) + " for: " + prompt[:40],
                  "stats": {{"models": {{}}, "tools": {{"totalCalls": 3}}}}}}))
""")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
os.environ["GEMINI_CLI_BIN"] = str(fake)

# Pre-seeded "clone" so tests never hit the network.
WORKSPACE = _tmp / "workspace"
os.environ["GEMINI_CLI_WORKSPACE"] = str(WORKSPACE)
repo = WORKSPACE / "octo__demo"
(repo / ".git").mkdir(parents=True)
(repo / "app.py").write_text("print('hello')\n")
(repo / "README.md").write_text("# demo\n")
