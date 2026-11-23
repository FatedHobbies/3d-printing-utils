"""Start the 3dpu Manager desktop app."""

import sys


def main() -> int:
    from threedpu.manager.app import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
