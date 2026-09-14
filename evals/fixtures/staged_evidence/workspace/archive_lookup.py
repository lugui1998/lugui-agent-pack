#!/usr/bin/env python3
"""Transparent, intentionally slow synthetic archive lookup for the scheduler probe."""
import json
import time


time.sleep(75)
print(json.dumps({
    "evidence_kind": "synthetic_stale_archive",
    "release": "Atlas",
    "version": "4.1.9",
    "record_date": "2026-08-01",
    "source_url": "https://release-archive.example.invalid/atlas/4.1.9",
    "status": "approved",
    "stale": True,
}))
