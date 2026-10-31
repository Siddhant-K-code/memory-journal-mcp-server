from datetime import datetime
from types import SimpleNamespace


def photo(number, *, taken="2026-10-25T12:00:00+05:30", location="River Town", **changes):
    values = {
        "uuid": f"00000000-0000-0000-0000-{number:012x}",
        "filename": "IMG_1001.jpg",
        "original_filename": "IMG_1001.jpg",
        "date": datetime.fromisoformat(taken),
        "place": SimpleNamespace(name=location) if location is not None else None,
        "persons": ["Person A", "Parent One"],
        "labels": ["River"],
        "keywords": ["Holiday"],
        "path": None,
        "path_edited": None,
        "path_derivatives": [],
        "favorite": False,
        "hidden": False,
        "intrash": False,
        "ismovie": False,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class FixtureDB:
    def __init__(self, photos):
        self.items = photos

    def photos(self):
        return list(self.items)


def library(preview=None):
    return FixtureDB(
        [
            photo(1, taken="2026-10-23T23:59:59+05:30"),
            photo(
                2, taken="2026-10-24T00:00:00+05:30", path_derivatives=[preview] if preview else []
            ),
            photo(
                3,
                taken="2026-10-30T23:59:59.999999+05:30",
                location="Hill Town",
                persons=["Person A"],
            ),
            photo(4, taken="2026-10-31T00:00:00+05:30"),
            photo(5, ismovie=True),
            photo(6, hidden=True),
            photo(7, intrash=True),
            photo(8, location=None, persons=[], labels=[], keywords=[]),
            photo(9, location=None, place=SimpleNamespace(name=None)),
        ]
    )
