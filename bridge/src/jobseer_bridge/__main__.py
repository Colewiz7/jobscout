import sys

from .config import Config

USAGE = "usage: jobseer-bridge [check]"


def main(argv=None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["check"]):
        sys.exit(USAGE)
    problem = None
    try:
        config = Config.from_env()
    except ValueError as error:
        problem = str(error)
    if problem:
        sys.exit("jobseer-bridge: %s" % problem)
    if args:
        from .check import run_check

        sys.exit(run_check(config))
    from .server import build

    server, _ = build(config)
    server.run()


if __name__ == "__main__":
    main()
