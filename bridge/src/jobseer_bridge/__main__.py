import sys

from .config import Config

USAGE = "usage: jobseer-bridge [check|serve]"


def main(argv=None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["check"], ["serve"]):
        sys.exit(USAGE)
    if args == ["serve"]:
        serve()
        return
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


def serve() -> None:
    problem = None
    try:
        from .remote import RemoteSettings
        from .server import build_remote

        settings = RemoteSettings.from_env()
        config = Config.from_env(readonly_default=True)
        server, _ = build_remote(config, settings)
    except ImportError:
        problem = "serve needs the remote extra: pip install 'jobseer-bridge[remote]'"
    except ValueError as error:
        problem = str(error)
    if problem:
        sys.exit("jobseer-bridge: %s" % problem)
    server.run("streamable-http")


if __name__ == "__main__":
    main()
