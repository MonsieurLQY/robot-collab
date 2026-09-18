import io
import logging
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from prompting.terminal_log import record_terminal


def test_records_console_text_without_changing_display(tmp_path):
    stdout, stderr = io.StringIO(), io.StringIO()
    path = tmp_path / "terminal.txt"
    logger = logging.getLogger("terminal_log_test")
    handler = logging.StreamHandler(stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    old_propagate = logger.propagate
    logger.propagate = False
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with record_terminal(path):
                print("模型回答：", end="")
                print("EXECUTE\nNAME LeftArm ACTION WAIT")
                sys.stderr.write("progress\rfinished\n")
                logger.warning("existing feedback")
            assert sys.stdout is stdout
            assert sys.stderr is stderr
            assert handler.stream is stderr
        expected_out = "模型回答：EXECUTE\nNAME LeftArm ACTION WAIT\n"
        expected_err = "progress\rfinished\nWARNING: existing feedback\n"
        assert stdout.getvalue() == expected_out
        assert stderr.getvalue() == expected_err
        # Byte comparison preserves carriage returns rather than normalizing them.
        assert path.read_bytes().decode("utf-8") == expected_out + expected_err
    finally:
        logger.removeHandler(handler)
        logger.propagate = old_propagate
        handler.close()


def test_exception_restores_streams_and_resume_appends(tmp_path):
    path = tmp_path / "terminal.txt"
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        with pytest.raises(RuntimeError, match="stopped"):
            with record_terminal(path):
                print("before failure")
                raise RuntimeError("stopped")
        assert sys.stdout is stdout
        assert sys.stderr is stderr
        with record_terminal(path):
            print("resumed")
        print("outside run")
    assert path.read_text() == "before failure\nresumed\n"
    assert stdout.getvalue() == "before failure\nresumed\noutside run\n"
