from __future__ import annotations

import argparse
import json

from fivefold.db import get_engine, init_db
from fivefold.reset import reset_generated_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete Fivefold-generated pipeline data.")
    parser.add_argument("--yes", action="store_true", help="Confirm the irreversible reset")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to reset without --yes")
    result = reset_generated_data(get_engine())
    init_db()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
