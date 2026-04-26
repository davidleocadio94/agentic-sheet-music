"""Shared pytest fixtures + back-compat shims for the Audiveris-era tests.

The Audiveris OMR module was removed in the Gemini-vision pivot (2026-04-25).
Tests that referenced `which_audiveris` are kept around but always skip,
because their `if which_audiveris() is None` guards now resolve to True.
"""

# Compatibility shim: tests still import this at module top-level.
# Returning None means "Audiveris not installed" → tests self-skip cleanly.
def which_audiveris():
    return None
