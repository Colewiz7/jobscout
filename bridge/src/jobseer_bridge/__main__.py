from .config import Config


def main() -> None:
    from .server import build

    server, _ = build(Config.from_env())
    server.run()


if __name__ == "__main__":
    main()
