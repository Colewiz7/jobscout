# jobseer bridge

Stdio tool server that lets a tool client manage JobSeer through the dashboard's
own HTTP API. It is a separate package: nothing here is part of the root
install, the image or CI.

## Test

    python3.12 -m venv .venv
    .venv/bin/pip install -r ../requirements.txt -e '.[test]'
    make test

Tests start the dashboard in-process with the demo store, so no database or
network is needed.
