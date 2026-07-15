"""Tests for ud_ledger — append-only signal audit trail."""
import csv
import os
import tempfile

import pytest

from ud_ledger import FIELDS, Ledger


def test_ledger_writes_header_and_rows():
    """Test that ledger writes header once and appends rows correctly."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ud_ledger.csv")
        led = Ledger(path)
        led.log(
            ts=1,
            asset="ETH",
            direction="UP",
            m=2,
            tier="ELITE",
            action="entered",
            ask=0.55,
            pnl=0.0,
            reason="ok",
        )
        led.log(
            ts=2,
            asset="BTC",
            direction="DOWN",
            m=1,
            tier="SKIP",
            action="skipped",
            reason="tier/hour skip",
        )
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))

        # Verify header
        assert list(rows[0].keys()) == FIELDS

        # Verify first row
        assert rows[0]["asset"] == "ETH"
        assert rows[0]["action"] == "entered"
        assert rows[0]["ts"] == "1"
        assert rows[0]["ask"] == "0.55"

        # Verify second row
        assert rows[1]["action"] == "skipped"
        assert rows[1]["ask"] == ""  # Unknown key not in kwargs
        assert rows[1]["asset"] == "BTC"


def test_ledger_no_header_on_second_instance():
    """Test that calling log() on a second instance doesn't write header again."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "ud_ledger.csv")

        # First instance writes header and first row
        led1 = Ledger(path)
        led1.log(ts=1, asset="ETH", direction="UP", action="entered", reason="first")

        # Second instance writes second row (header already exists)
        led2 = Ledger(path)
        led2.log(ts=2, asset="BTC", direction="DOWN", action="skipped", reason="second")

        # Read all rows
        with open(path, newline="") as f:
            content = f.read()
            lines = [line.rstrip("\r") for line in content.strip().split("\n")]

        # Should have header + 2 data rows = 3 lines total
        assert len(lines) == 3, f"Expected 3 lines (header + 2 rows), got {len(lines)}: {lines}"

        # Verify header line (first line)
        assert lines[0] == ",".join(FIELDS)

        # Verify data rows via DictReader
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["asset"] == "ETH"
        assert rows[1]["asset"] == "BTC"
