"""Entry point for the homeops coordinator daemon."""

import logging
import sys


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    from coordinator.daemon import run

    run()


if __name__ == "__main__":
    main()
