"""Fetch the external uplift benchmark.

    python -m eval.fetch_hillstrom

Kevin Hillstrom's MineThatData e-mail challenge (2008): 64,000 customers
randomised three ways between a men's e-mail, a women's e-mail and no e-mail.
It is the standard small public uplift dataset, and it is used here to check
the Qini implementation against an experiment nobody on this project ran.

Not committed - 4 MB, and a dataset in version control is a dataset that
silently goes stale. The checksum is pinned instead, so a changed file fails
the test rather than quietly moving the numbers.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

URL = (
    "http://www.minethatdata.com/"
    "Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv"
)
DEST = Path(__file__).resolve().parent.parent / "data" / "hillstrom.csv"
SHA256 = "0e5893329d8b93cefecc571777672028290ab69865718020c78c7284f291aece"
EXPECTED_BYTES = 3_964_977


def main() -> int:
    if DEST.exists():
        digest = hashlib.sha256(DEST.read_bytes()).hexdigest()
        if digest == SHA256:
            print(f"already present and verified: {DEST}")
            return 0
        print(f"checksum mismatch at {DEST}; re-downloading")

    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetching {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=60) as resp:
            payload = resp.read()
    except Exception as exc:
        print(f"download failed: {type(exc).__name__}: {exc}")
        print("The benchmark tests will skip; the rest of the suite is unaffected.")
        return 1

    digest = hashlib.sha256(payload).hexdigest()
    if digest != SHA256:
        print("REFUSING to write: checksum does not match the pinned value.")
        print(f"  expected {SHA256}")
        print(f"  got      {digest}")
        print("The upstream file changed. Verify it before updating the pin.")
        return 2

    DEST.write_bytes(payload)
    print(f"  -> {DEST}  ({len(payload):,} bytes, sha256 verified)")
    print("\nRun: pytest -q tests/test_qini_hillstrom.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
