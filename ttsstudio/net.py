"""A CA bundle for every HTTPS call this app makes outside a worker venv.

Python's ``ssl`` module resolves trusted certificate authorities from the
system, and both macOS and Windows expose that store in a way the interpreter
that ships inside the app can see — *except* the interpreter PyInstaller
freezes into the app bundle, which carries none of the OS integration that
python.org or Homebrew builds set up. Every plain ``urlopen()`` call in a
frozen build fails with ``CERTIFICATE_VERIFY_FAILED``, which is silent in
development (the dev venv's Python resolves certs fine) and only surfaces
once a build is actually installed.

``certifi`` ships its own bundle as a data file, so pointing every context at
it works identically frozen or not. edge-tts already does this internally;
this module is for the two places that talk to fresh sockets directly:
downloading engines in :mod:`runtime` and calling ElevenLabs in :mod:`cloud`.
"""

from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    import certifi

    return ssl.create_default_context(cafile=certifi.where())
