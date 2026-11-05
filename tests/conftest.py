import tkinter as tk
import pytest


@pytest.fixture(scope='session')
def native_root():
    """Exercise the pages in one Tcl interpreter, just like the desktop app."""
    window=tk.Tk()
    window.withdraw()
    yield window
    window.destroy()
