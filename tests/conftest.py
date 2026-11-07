import tkinter as tk
import gc
import pytest


@pytest.fixture(autouse=True)
def collect_native_widgets_on_test_thread(request):
    """Don't let a later HTTP worker finalize cyclic Tk variables from UI tests."""
    yield
    if 'native_root' in request.fixturenames:
        gc.collect()


@pytest.fixture(scope='session')
def native_root():
    """Exercise the pages in one Tcl interpreter, just like the desktop app."""
    window=tk.Tk()
    window.withdraw()
    yield window
    window.destroy()
