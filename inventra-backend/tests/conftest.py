import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="inventra-test-")
os.environ["RUNTIME_DIR"] = _tmp
os.environ["WATCHER_ENABLED"] = "0"
os.environ["AGENT_WATCH_ENABLED"] = "0"  # tests call the watcher agent directly, without background threads
for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    os.environ[k] = ""
