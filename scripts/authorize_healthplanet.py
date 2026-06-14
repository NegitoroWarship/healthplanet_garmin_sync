"""One-time HealthPlanet OAuth authorization helper.

Run this once interactively to obtain and persist the initial access/refresh
tokens. Afterwards the sync job refreshes tokens automatically.

Usage:
    python -m scripts.authorize_healthplanet
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a script from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.healthplanet import build_authorize_url, exchange_code_for_tokens  # noqa: E402


def main() -> int:
    config = load_config()
    url = build_authorize_url(config)
    print("\n1) Open this URL in a browser and log in / approve access:\n")
    print(f"   {url}\n")
    print(
        "2) After approving, you are redirected to the success page which shows\n"
        "   an authorization code (in the page / its title). Copy that code.\n"
    )
    code = input("3) Paste the authorization code here: ").strip()
    if not code:
        print("No code entered; aborting.", file=sys.stderr)
        return 1

    tokens = exchange_code_for_tokens(config, code)
    print(f"\nSuccess. Tokens saved to: {config.hp_token_file}")
    print(f"  access_token expires_in: {tokens.get('expires_in')} seconds")
    print(f"  refresh_token present: {bool(tokens.get('refresh_token'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
