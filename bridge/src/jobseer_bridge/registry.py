from dataclasses import dataclass, field

KINDS = ("read", "write", "confirm")


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str
    description: str
    routes: tuple = ()
    hints: dict = field(default_factory=dict)


# routes name the dashboard paths a tool wraps, for the coverage test
def tool(kind: str, description: str, routes=(), **hints):
    assert kind in KINDS

    def mark(fn):
        fn.spec = Spec(fn.__name__, kind, description, tuple(routes), hints)
        return fn

    return mark


def annotations(spec: Spec) -> dict:
    read = spec.kind == "read"
    base = {
        "readOnlyHint": read,
        "destructiveHint": spec.kind == "confirm",
        "idempotentHint": read,
        "openWorldHint": False,
    }
    return {**base, **spec.hints}


def collect(obj) -> list:
    found = []
    for name in dir(type(obj)):
        member = getattr(type(obj), name, None)
        spec = getattr(member, "spec", None)
        if spec is not None:
            found.append((spec, getattr(obj, name)))
    return found
