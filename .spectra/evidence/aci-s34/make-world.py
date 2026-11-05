"""ACI-S34: write tests/fixtures/adr_mapping/world.json (the before/after file maps).

Run from the repository root: python make-world.py <fixture-dir>. Texts are ASCII except the one
accented character in ADR-0001 (written as a JSON escape).
"""

import json
import sys
from pathlib import Path

ADR1 = (
    "# ADR-0001: Pricing\n\n## Status\n\nAccepted\n\n## Decision\n\n"
    "Charges are computed by the charge function and doubled at the pricing tier.\n"
    "A café surcharge is never added. Refunds follow the refund function, and the\n"
    "audit trail is written by audit.\n\n## Consequences\n\nTotals depend on the multiplier.\n"
)
ADR2 = "# ADR-0002: Audit\n\n## Decision\n\nEvery posting is audited and the audit is kept.\n"
ADR3 = "# ADR-0003: Legacy\n\n## Decision\n\nThe legacy path is retired after the migration.\n"

BEFORE = {
    "docs/adr/0001-pricing.md": ADR1,
    "docs/adr/0002-audit.md": ADR2,
    "docs/adr/0003-legacy.md": ADR3,
    "src/pricing.py": "def charge(amount):\n    total = amount * 2\n    return total\n",
    "src/refunds.py": "def refund(amount):\n    return amount\n",
    "src/audit.py": "def audit(entry):\n    record = entry\n    return record\n",
    "src/ledger.py": "def post(entry):\n    return entry\n",
    "src/reports.py": "def summary(rows):\n    return len(rows)\n",
    "src/legacy.py": "def retire(item):\n    return None\n",
    "src/rename.py": (
        "def compute_total(items):\n    total = 0\n    for item in items:\n"
        "        total = total + item\n    return total\n"
    ),
    "src/fragile.py": "def keep(a):\n    return a\n",
    "config/limits.yml": "max_charge: 10\nmax_refund: 5\n",
    "src/settings.py": "def limit():\n    return 10\n",
    "src/constants.py": "def one():\n    return 1\n",
    "src/dupes.py": "def twin(a):\n    return a + 1\n\n\ndef twin(a):\n    return a + 2\n",
    "src/twins.py": "def dual(a):\n    return a + 1\n\n\ndef dual(a):\n    return a + 2\n",
    "src/account.py": (
        "class Account:\n    def open(self):\n        return 1\n\n"
        "    def close(self):\n        return 2\n"
    ),
}

AFTER = {
    "docs/adr/0001-pricing.md": ADR1,
    "docs/adr/0002-audit.md": ADR2 + "\n## Notes\n\nAdded after the decision.\n",
    "docs/adr/0003-legacy.md": None,
    "src/pricing.py": "def charge(amount):\n    total = amount * 3\n    return total\n",
    "src/refunds.py": "def refund(amount, reason):\n    return amount\n",
    "src/audit.py": (
        "def audit(entry):\n    record = entry\n    # keep the trail\n    return record\n"
    ),
    "src/ledger.py": "# ledger note\ndef post(entry):\n    return entry\n",
    "src/reports.py": "def summary(rows):\n    return len(rows)\n",
    "src/legacy.py": None,
    "src/legacy_v2.py": "def retire(item):\n    item.closed = True\n    return item\n",
    "src/rename.py": (
        "def calculate_total(items):\n    total = 0\n    for item in items:\n"
        "        total = total + item\n    return total\n"
    ),
    "src/fragile.py": "def keep(:\n    return a\n",
    "config/limits.yml": "max_charge: 20\nmax_refund: 5\n",
    "src/settings.py": "def limit():\n    return 20\n",
    "src/constants.py": "def one():\n    return 1\n",
    "src/dupes.py": "def twin(a):\n    return a + 2\n",
    "src/twins.py": "def dual(a):\n    return a + 1\n\n\ndef dual(a):\n    return a + 3\n",
    "src/account.py": (
        "class Account:\n    def open(self):\n        return 1\n\n"
        "    def close(self):\n        return 3\n"
    ),
}

if __name__ == "__main__":
    out = Path(sys.argv[1]) / "world.json"
    text = json.dumps({"before": BEFORE, "after": AFTER}, indent=1, sort_keys=True) + "\n"
    out.write_text(text, encoding="ascii")
    print(out, len(text))
