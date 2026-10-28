import sys

import httpx

from .client import Client, denial_message, is_denial
from .config import Config


# live checks must stay harmless
def run_check(config: Config, client: Client | None = None, out=None) -> int:
    out = out or sys.stdout
    client = client or Client(config)
    lines = ["target: %s" % config.origin, "auth: %s" % config.auth_mode]
    try:
        reply = client.probe()
    except httpx.TransportError as error:
        lines += ["http status: none", "auth accepted: unknown", "error: cannot reach target: %s" % config.redact(str(error))]
        code = 1
    else:
        lines.append("http status: %d" % reply.status_code)
        code = 1
        if is_denial(reply):
            lines += ["auth accepted: no", "error: " + denial_message(reply)]
        elif reply.status_code >= 400:
            lines += ["auth accepted: yes", "error: the dashboard answered with an error"]
        else:
            try:
                user = str(reply.json()["user"])
            except (ValueError, KeyError, TypeError):
                lines += ["auth accepted: yes", "error: the reply is not a dashboard session"]
            else:
                lines += ["auth accepted: yes", "session user: %s" % config.redact(user)]
                code = 0
    finally:
        client.close()
    out.write(config.redact("\n".join(lines)) + "\n")
    return code
