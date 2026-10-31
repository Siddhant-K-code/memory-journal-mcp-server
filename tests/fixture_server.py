"""Test-only stdio server with synthetic records; never reads Apple Photos."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

import server
from tests.fixtures import library


def load_fixture():
    Path(sys.argv[2]).touch()
    if "--deny" in sys.argv:
        raise PermissionError("synthetic denied library")
    return library(sys.argv[1])


server.photos_loader = server.PhotosDBLoader(load_fixture)
asyncio.run(server.main())
