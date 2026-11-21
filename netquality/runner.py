"""Runs the measurement engine in a child process so its threads never compete with the UI for the GIL.

Messages to the parent (via a multiprocessing queue):
  ("event", name, data)   live progress, same as SpeedTest's emit callback
  ("done", result)        final result dict
  ("failed", message)     cancelled or crashed
Kept free of Qt imports so the child process starts quickly.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback


def _lower_priority() -> None:
    """The localhost test saturates every core; below-normal priority keeps the UI responsive meanwhile."""
    try:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(5)
    except (OSError, AttributeError):
        pass


def run_engine(cfg_kwargs: dict, queue, cancel_event) -> None:
    from .engine import Cancelled, SpeedTest, TestConfig

    _lower_priority()

    test = SpeedTest(TestConfig(**cfg_kwargs), lambda e, d: queue.put(("event", e, d)))

    def watch_cancel():
        cancel_event.wait()
        test.cancel()

    threading.Thread(target=watch_cancel, daemon=True).start()
    try:
        queue.put(("done", test.run()))
    except Cancelled:
        queue.put(("failed", "Test cancelled."))
    except Exception as e:  # surface anything unexpected in the UI
        queue.put(("failed", f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
