from __future__ import annotations

import getpass

from fivefold.auth import hash_password

if __name__ == "__main__":
    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    print(hash_password(password))

