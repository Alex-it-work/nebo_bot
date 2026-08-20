"""Entry point for the Nebo game bot."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.bot import NeboBot
from src.config import Config, ConfigError
from src import config as config_module

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def force_utf8_output() -> None:
    """Make stdout and stderr carry Cyrillic safely.

    Account names and site messages are Russian. On Windows both streams
    default to the ANSI code page when redirected, which cannot encode
    Cyrillic, so anything printed before this would raise.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


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
    parser.add_argument(
        "-a",
        "--account",
        action="append",
        metavar="NAME",
        help="run only this account; repeat the flag to select several",
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="print the configured accounts and exit",
    )
    return parser.parse_args(argv)


def select_accounts(configs: list[Config], wanted: list[str] | None) -> list[Config]:
    """Narrow the configured accounts to those named on the command line.

    Args:
        configs: Every configured account, in file order.
        wanted: Names requested, or None for all of them.

    Returns:
        The selected configurations, in file order.

    Raises:
        ConfigError: If a requested name is not configured.
    """
    if not wanted:
        return configs

    by_name = {config.username: config for config in configs}
    missing = [name for name in wanted if name not in by_name]
    if missing:
        raise ConfigError(
            f"No such account(s): {', '.join(missing)}. Configured: {', '.join(by_name)}"
        )
    return [config for config in configs if config.username in set(wanted)]


def run_account(config: Config, login_only: bool) -> bool:
    """Play one account from login to logout.

    Failures are contained here: with thirty accounts queued, one broken login
    must not take the rest of the run down with it.

    Returns:
        True if the account finished what it was asked to do.
    """
    logger.info("=== %s ===", config.username)
    bot = NeboBot(config)
    try:
        if not bot.start():
            logger.error("%s: login failed", config.username)
            return False
        if login_only:
            logger.info("%s: login check succeeded", config.username)
            return True
        return bot.run()
    except KeyboardInterrupt:
        raise
    except Exception:
        logger.exception("%s: unexpected error", config.username)
        return False
    finally:
        bot.stop()


def setup_logging(config: Config) -> None:
    """Configure logging to stdout and, when configured, to a file.

    Creates the log directory if needed; a missing directory would otherwise
    make logging fail at startup.
    """
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
    # Before any output: account names and errors below may be Cyrillic.
    force_utf8_output()

    args = parse_args(argv)

    # Logging is not configured yet, so configuration errors go to stderr.
    try:
        configs = select_accounts(config_module.load_all(args.config), args.account)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if args.list_accounts:
        for config in configs:
            print(f"{config.username}	{config.maze_rounds} maze(s)")
        return 0

    # Logging settings come from the first account; they are global anyway.
    setup_logging(configs[0])
    logger.info("Running %d account(s)", len(configs))

    results: dict[str, bool] = {}
    try:
        for position, config in enumerate(configs, start=1):
            logger.info("Account %d of %d", position, len(configs))
            results[config.username] = run_account(config, args.login_only)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
        return 130

    if len(configs) > 1:
        logger.info("--- summary ---")
        for name, ok in results.items():
            logger.info("%-20s %s", name, "ok" if ok else "FAILED")

    succeeded = sum(results.values())
    logger.info("Finished: %d of %d account(s) succeeded", succeeded, len(results))
    return 0 if succeeded == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
