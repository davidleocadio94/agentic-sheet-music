"""Back-compat shims for the Audiveris-era tests after the Gemini pivot."""

def which_audiveris():
    """Audiveris was removed; tests guarded by `if which_audiveris() is None`
    will all skip cleanly thanks to this stub.
    """
    return None
