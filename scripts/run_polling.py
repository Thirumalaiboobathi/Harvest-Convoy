"""Local dev runner: long-polls Telegram's getUpdates instead of running a
webhook server, so the Phase 4 gate can be verified without a public HTTPS
endpoint. NOT the production path -- AgentCore Runtime with a real webhook
is Phase 6. This exists solely so the registration flow can be exercised
locally with just a bot token, no ngrok/tunnel/deployment needed.

webhook.handle_update() has required `storage`/`season_id` parameters
since Phase 5 (ADR-005 Decision 4) -- constructed/parsed here via the same
get_storage() seam and --season-id default every other script uses.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx
from dotenv import load_dotenv

from harvest_convoy.storage import get_storage
from harvest_convoy.telegram import webhook
from harvest_convoy.telegram.client import TelegramClient

POLL_TIMEOUT_SECONDS = 30
DEFAULT_SEASON_ID = "2026-kuruvai"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--season-id", default=DEFAULT_SEASON_ID,
        help=f"Season ID for fairness-ledger writes on escalation resolution (default: {DEFAULT_SEASON_ID}).",
    )
    args = parser.parse_args()

    load_dotenv()
    client = TelegramClient()
    if not client.token:
        print(
            "TELEGRAM_BOT_TOKEN not set. Copy .env.example to .env and fill "
            "in a real token from @BotFather, or export it directly."
        )
        sys.exit(1)

    storage = get_storage()

    print(f"Polling as bot token ...{client.token[-6:]}. Press Ctrl+C to stop.")
    offset: int | None = None

    while True:
        params: dict = {"timeout": POLL_TIMEOUT_SECONDS}
        if offset is not None:
            params["offset"] = offset
        try:
            response = httpx.get(
                f"https://api.telegram.org/bot{client.token}/getUpdates",
                params=params,
                timeout=POLL_TIMEOUT_SECONDS + 5.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001 -- keep polling, don't crash the loop
            print(f"getUpdates failed: {exc}; retrying in 3s")
            time.sleep(3)
            continue

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            print(f"update: {update}")
            webhook.handle_update(client, update, storage, args.season_id)


if __name__ == "__main__":
    main()
