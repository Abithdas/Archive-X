"""Every `uses:` in the GitHub workflows must name a full commit (40 hex characters), not a tag or branch: a tag can be moved by its owner,
so a compromised action could run in our build with our secrets. Dependabot keeps the commits current (the tag stays as a comment).

    python tools/sca/check_action_pins.py [workflows folder]      exit 1 and a list when something is not pinned
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)")
PINNED = re.compile(r"@[0-9a-f]{40}$")


def unpinned(folder: Path) -> list[str]:
    bad = []
    for path in sorted(folder.glob("*.y*ml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = USES.match(line)
            if not m:
                continue
            ref = m.group(1)
            if ref.startswith("./") or ref.startswith("docker://"):   # local actions are our own code; images are out of scope here
                continue
            if not PINNED.search(ref):
                bad.append(f"{path.name}:{number}: {ref}")
    return bad


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".github/workflows")
    bad = unpinned(folder)
    for b in bad:
        print("not pinned to a commit:", b)
    if not bad:
        print(f"all actions in {folder} are pinned to a commit")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
