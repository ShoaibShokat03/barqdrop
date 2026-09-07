"""Launcher used by Run.bat and by the PyInstaller build."""
import multiprocessing
import sys

from barqdrop.gui import run

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(run())
