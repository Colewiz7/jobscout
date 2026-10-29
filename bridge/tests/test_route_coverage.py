import pathlib

import jobscout.dashboard as dashboard
from jobseer_bridge.bridge import Bridge
from jobseer_bridge.config import Config
from route_scan import mutating_tokens


def real_tokens():
    return mutating_tokens(pathlib.Path(dashboard.__file__).read_text())


def wrapped_tokens():
    bridge = Bridge(Config())
    return {route for spec, _ in bridge.tools() for route in spec.routes if spec.kind != "read"}


def test_scanner_sees_a_new_route():
    source = '''
class DashboardHandler:
    def do_POST(self):
        if parsed.path == "/api/v1/widgets":
            pass
    def do_GET(self):
        if parsed.path == "/api/v1/only-read":
            pass
'''
    assert mutating_tokens(source) == {"/api/v1/widgets"}


def test_scanner_reads_the_real_handler():
    tokens = real_tokens()
    assert len(tokens) > 40
    assert {"/api/v1/hidden", "/api/v1/interviews/", "/hide", "/next-step", "/api/v1/profile"} <= tokens


def test_every_mutating_route_is_wrapped():
    missing = real_tokens() - wrapped_tokens()
    assert not missing, "wrap these dashboard routes in a tool: %s" % sorted(missing)


def test_nothing_listed_has_gone_stale():
    stale = wrapped_tokens() - real_tokens()
    assert not stale, "these routes no longer exist in the dashboard: %s" % sorted(stale)
