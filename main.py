"""Entry point for the Nebo game bot."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.bot import NeboBot
from src.config import Config, ConfigError

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Automation bot for the nebo.mobi game")
    parser.add_argument(
        "-c",
        "--config",
        default="config/config.yml",
        help="path to the configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="authenticate, report the result and exit without running features",
    )
    return parser.parse_args(argv)


def setup_logging(config: Config) -> None:
    """Configure logging to stdout and, when configured, to a file.

    Creates the log directory if needed; a missing directory would otherwise
    make logging fail at startup.
    """
    # Log messages quote Russian text from the site. When stdout is redirected
    # on Windows it defaults to the ANSI code page, which cannot encode
    # Cyrillic and would raise mid-log; force UTF-8 and never fail on output.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if config.log_file:
        log_path = Path(config.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(level=config.numeric_log_level, format=_LOG_FORMAT, handlers=handlers)


def main(argv: list[str] | None = None) -> int:
    """Run the bot.

    Returns:
        Process exit code: 0 on success, 1 on failure, 130 on interrupt.
    """
    args = parse_args(argv)

    # Logging is not configured yet, so configuration errors go to stderr.
    try:
        bot = NeboBot(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(bot.config)

    try:
        if not bot.start():
            logger.error("Bot failed to start")
            return 1

        if args.login_only:
            logger.info("Login check succeeded")
            return 0

        return 0 if bot.run() else 1

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
        return 130
    except Exception:
        logger.exception("Unexpected error")
        return 1
    finally:
        # Only reached once the bot exists, so logging out is always safe here.
        bot.stop()
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
