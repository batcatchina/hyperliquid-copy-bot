#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path

from hltrade_bot import CopyBot


def parse_args(argv):
    mode = "monitor"
    duration_min = 0
    if len(argv) >= 2:
        arg = argv[1].strip().lower()
        if arg in ("monitor", "live"):
            mode = arg
    if len(argv) >= 3:
        try:
            duration_min = int(argv[2])
        except ValueError:
            duration_min = 0
    return mode, duration_min


def main():
    try:
        mode, duration_min = parse_args(sys.argv)
        config_path = Path(__file__).resolve().parent / "config.json"

        bot = CopyBot(config_path=str(config_path))
        bot.live = (mode == "live")

        print(f"Starting Hyperliquid CopyBot in '{mode}' mode")
        print(f"Run duration: {'infinite' if duration_min == 0 else f'{duration_min} minute(s)'}")

        start_time = time.time()
        bot.run()

        if duration_min > 0:
            end_time = start_time + duration_min * 60
            while time.time() < end_time:
                time.sleep(1)
            print("Requested duration reached, stopping bot...")
            bot.running = False

    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
