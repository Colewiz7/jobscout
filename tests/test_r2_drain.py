"""Draining the Career Connect bucket."""
import pathlib

import pytest

from jobscout.sources import careerconnect

FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "careerconnect_infrastructure.eml").read_bytes()

CONFIRMATION = (b"From: forwarding-noreply@google.com\r\n"
                b"Subject: Forwarding Confirmation\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"Please confirm forwarding.\r\n")


class FakeBucket:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.deleted, self.put = [], []

    def keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key):
        return self.objects[key]

    def put_(self, key, body):
        self.objects[key] = body

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def move(self, key, new_key):
        self.put.append(new_key)
        self.objects[new_key] = self.objects.pop(key)


def test_an_alert_parses_and_is_marked_for_deletion():
    bucket = FakeBucket({careerconnect.INBOX + "alert.eml": FIXTURE})
    postings, parsed, failed = careerconnect.drain(bucket)
    assert len(postings) == 17
    assert parsed == [careerconnect.INBOX + "alert.eml"]
    assert failed == []
    # drain never deletes; that is the caller's job, after the commit
    assert bucket.deleted == []


def test_a_message_with_no_jobs_is_set_aside_not_deleted():
    """The forwarding confirmations are real mail that are not job alerts."""
    bucket = FakeBucket({careerconnect.INBOX + "confirm.eml": CONFIRMATION})
    postings, parsed, failed = careerconnect.drain(bucket)
    assert postings == [] and parsed == []
    assert failed == [careerconnect.INBOX + "confirm.eml"]


def test_retire_deletes_only_what_parsed_and_files_the_rest():
    bucket = FakeBucket({
        careerconnect.INBOX + "alert.eml": FIXTURE,
        careerconnect.INBOX + "confirm.eml": CONFIRMATION,
    })
    postings, parsed, failed = careerconnect.drain(bucket)
    careerconnect.retire(bucket, parsed, failed)
    assert bucket.deleted == [careerconnect.INBOX + "alert.eml"]
    assert bucket.put == [careerconnect.FAILED + "confirm.eml"]


def test_one_unreadable_object_does_not_lose_the_others():
    class Flaky(FakeBucket):
        def get(self, key):
            if "bad" in key:
                raise OSError("read failed")
            return super().get(key)

    bucket = Flaky({
        careerconnect.INBOX + "bad.eml": b"",
        careerconnect.INBOX + "alert.eml": FIXTURE,
    })
    postings, parsed, failed = careerconnect.drain(bucket)
    assert len(postings) == 17
    # unreadable is left exactly where it is: not parsed, not filed away
    assert careerconnect.INBOX + "bad.eml" not in parsed + failed
