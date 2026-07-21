"""The startup banner must not be able to kill the process.

`python run.py > server.log` on Windows gave stdout the ANSI code page, which
cannot encode the arrow in the banner or the accents in the network warning.
The app died with UnicodeEncodeError before binding its port — unrunnable
under any redirect, pipe or service wrapper.
"""

from __future__ import annotations

import io
import sys

import pytest

from app.server import build_run_options, make_console_utf8_safe


@pytest.fixture
def cp1252_stdout(monkeypatch):
    """stdout as Windows hands it over when output is redirected."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    return stream


class TestConsoleEncoding:
    def test_the_banner_arrow_would_crash_an_ansi_console(self, cp1252_stdout):
        """Guards the regression: without the fix, this is the crash.

        Written against the stream object rather than `print`, because pytest
        captures stdout at the file-descriptor level — a monkeypatched
        `sys.stdout` is not what `print` ends up writing to under capture.
        """
        with pytest.raises(UnicodeEncodeError):
            cp1252_stdout.write("Markdown Studio → http://127.0.0.1:5000\n")
            cp1252_stdout.flush()

    def test_the_banner_survives_after_the_fix(self, cp1252_stdout):
        make_console_utf8_safe()

        sys.stdout.write("Markdown Studio → http://127.0.0.1:5000\n")
        sys.stdout.flush()

        assert sys.stdout.encoding == "utf-8"

    def test_accented_warnings_survive(self, cp1252_stdout):
        """The non-loopback warning is the one a user most needs to read."""
        make_console_utf8_safe()
        options = build_run_options({"HOST": "0.0.0.0", "PORT": 5000, "DEBUG": True})

        assert options.warnings
        for warning in options.warnings:
            sys.stdout.write(f"  AVISO: {warning}\n")
        sys.stdout.flush()

    def test_a_stream_without_reconfigure_is_left_alone(self, monkeypatch):
        """Something else already wrapped stdout — don't fight it."""

        class Wrapped(io.StringIO):
            reconfigure = None

        monkeypatch.setattr(sys, "stdout", Wrapped())
        monkeypatch.setattr(sys, "stderr", Wrapped())

        make_console_utf8_safe()  # must not raise

    def test_a_detached_stream_does_not_raise(self, monkeypatch):
        class Detached:
            def reconfigure(self, **_kwargs):
                raise ValueError("underlying buffer has been detached")

        monkeypatch.setattr(sys, "stdout", Detached())
        monkeypatch.setattr(sys, "stderr", Detached())

        make_console_utf8_safe()  # must not raise
