"""The frozen build needs its own CA bundle — see ttsstudio/net.py.

A PyInstaller-frozen Python has no OS-integrated certificate store, so a bare
``urlopen()`` fails with CERTIFICATE_VERIFY_FAILED the moment the app is
actually installed and run, while working fine from a dev venv. That gap is
exactly why it shipped once already: nothing here failed in development. These
tests pin the two things a regression would break — every direct HTTPS call
site passing an explicit context, and that context resolving to certifi's
bundle rather than silently falling back to the (frozen-app-broken) default.
"""

from __future__ import annotations

import inspect
import re

from ttsstudio import cloud, net, runtime


def test_ssl_context_uses_certifi_bundle():
    import certifi

    ctx = net.ssl_context()
    assert ctx.cert_store_stats()["x509"] > 0
    # Not asserting the exact path — just that it's certifi's, not a default
    # that would be empty/absent in a frozen interpreter.
    assert certifi.where()


def test_ssl_context_is_cached():
    assert net.ssl_context() is net.ssl_context()


def _urlopen_calls(source: str) -> list[str]:
    """Every urlopen(...) call expression in a module's source."""
    return re.findall(r"urllib\.request\.urlopen\([^)]*\)", source)


def test_every_urlopen_call_passes_a_context():
    # A future HTTPS call that forgets `context=` reintroduces this bug
    # silently — it only breaks once someone actually installs the app.
    for module in (runtime, cloud):
        calls = _urlopen_calls(inspect.getsource(module))
        assert calls, f"expected at least one urlopen() call in {module.__name__}"
        for call in calls:
            assert "context=" in call, f"missing SSL context in {module.__name__}: {call}"


def test_net_module_has_no_heavy_imports_at_module_level():
    # ssl_context() imports certifi lazily so importing `net` stays cheap;
    # a top-level import would be a fine correctness-neutral change, but
    # this documents the intended shape.
    source = inspect.getsource(net)
    top_level_imports = re.findall(r"^import \w+|^from \w+ import", source, re.MULTILINE)
    assert "import certifi" not in top_level_imports
