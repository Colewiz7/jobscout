"""A broken run record must never change how a scout run ends."""
import argparse
import logging

import psycopg

from jobscout import __main__ as cli
from jobscout import db as database


class DeadConnection:
    """Every call fails, including the cleanup ones."""

    closed = False

    def rollback(self):
        raise psycopg.OperationalError("connection is gone")

    def close(self):
        raise psycopg.OperationalError("connection is gone")


def test_a_dead_connection_does_not_raise_from_the_start_record(monkeypatch, caplog):
    def broken(*a, **k):
        raise psycopg.OperationalError("write failed")

    monkeypatch.setattr(database, "start_scout_run", broken)
    with caplog.at_level(logging.WARNING):
        assert cli._record_start(DeadConnection()) is None
    assert "could not record the run start" in caplog.text


def test_a_dead_connection_does_not_raise_from_the_finish_record(monkeypatch, caplog):
    def broken(*a, **k):
        raise psycopg.OperationalError("write failed")

    monkeypatch.setattr(database, "finish_scout_run", broken)
    with caplog.at_level(logging.WARNING):
        cli._record_finish(DeadConnection(), 7, 0, {})
    assert "could not record the run result" in caplog.text


def test_the_exit_code_survives_a_dead_record_connection(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(database, "connect", lambda dsn: DeadConnection())
    monkeypatch.setattr(database, "try_scout_lock", lambda conn: True)

    def broken(*a, **k):
        raise psycopg.OperationalError("write failed")

    monkeypatch.setattr(database, "start_scout_run", lambda *a, **k: 5)
    monkeypatch.setattr(database, "finish_scout_run", broken)
    monkeypatch.setattr(cli, "_run_guarded", lambda args, stats: 4)
    assert cli.cmd_run(argparse.Namespace(config=None)) == 4


def test_a_run_that_raises_keeps_its_own_error_not_the_cleanup_one(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(database, "connect", lambda dsn: DeadConnection())
    monkeypatch.setattr(database, "try_scout_lock", lambda conn: True)
    monkeypatch.setattr(database, "start_scout_run", lambda *a, **k: 5)
    monkeypatch.setattr(database, "finish_scout_run", lambda *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("x")))

    def crash(args, stats):
        raise RuntimeError("source blew up")

    monkeypatch.setattr(cli, "_run_guarded", crash)
    try:
        cli.cmd_run(argparse.Namespace(config=None))
    except RuntimeError as error:
        assert str(error) == "source blew up"
    else:
        raise AssertionError("the run's own error was swallowed")
