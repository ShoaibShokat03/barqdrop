"""Entry point: python -m barqdrop"""
import sys


def main() -> int:
    from .gui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
