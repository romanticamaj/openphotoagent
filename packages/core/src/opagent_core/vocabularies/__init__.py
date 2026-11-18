"""Tag vocabularies shared by tagging (worker, opagent_inference) and search (api, opagent_search).

Package data so it ships in the wheel (ADR-0023). It lives in core because both sides read it and the API side
must not import inference packages (invariant 1). This module only names the directory; reading is the caller's I/O.
"""

from pathlib import Path

VOCAB_DIR = Path(__file__).resolve().parent
