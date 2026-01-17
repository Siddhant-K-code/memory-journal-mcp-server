import logging
import re
import threading
from collections import Counter
from typing import List, Optional
import osxphotos
from datetime import datetime, timedelta
from mcp.server import FastMCP
from dateparser import parse
from osxphotos import QueryOptions
from thefuzz import fuzz

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(filename)s - %(lineno)d - %(message)s",
    handlers=[
        logging.FileHandler("smart_photo_journal.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class PhotosDBLoader:
    def __init__(self):
        self._db: Optional[osxphotos.PhotosDB] = None
        self._loading = True
        self._error: Optional[str] = None
        self.start_loading()

    def start_loading(self):
        def load():
            try:
                logger.info("Starting PhotosDB load...")
                self._db = osxphotos.PhotosDB()
                logger.info(f"PhotosDB loaded successfully. Found {len(self._db.photos())} photos.")
                self._loading = False
            except Exception as e:
                logger.error(f"Failed to load PhotosDB: {e}")
                self._error = str(e)
                self._loading = False

        thread = threading.Thread(target=load)
        thread.daemon = True
        thread.start()

    @property
    def is_ready(self) -> bool:
        return self._db is not None and not self._loading

    @property
    def db(self) -> osxphotos.PhotosDB:
        if self._loading:
            raise Exception("PhotosDB is still loading. Please try again in a moment.")
        if self._error:
            raise Exception(f"PhotosDB failed to load: {self._error}")
        if self._db is None:
            raise Exception("PhotosDB not initialized")
        return self._db


# Global PhotosDB loader instance
photos_loader = PhotosDBLoader()

# Initialize FastMCP server
mcp = FastMCP("smart-photo-journal")


def fuzzy_match_search(text: str, search_term: str, threshold=60) -> bool:
    """Use fuzzy matching for more flexible text searching"""
    if not text or not search_term:
        return False
    return fuzz.ratio(text.lower(), search_term.lower()) > threshold


def get_photo_details(photo: osxphotos.PhotoInfo) -> dict:
    """Enhanced photo details with reliable filename extraction"""
    filename = (
        getattr(photo, "original_filename", None)
        or getattr(photo, "title", None)
        or (photo.path.split("/")[-1] if photo.path else None)
        or photo.filename
    )

    return {
        "filename": filename,
        "date": photo.date.strftime("%Y-%m-%d %H:%M:%S") if photo.date else "Unknown",
        "location": photo.place.name if photo.place else "Unknown",
        "path": photo.path,
        "persons": photo.persons or [],
        "labels": getattr(photo, "labels", []) or [],
        "keywords": getattr(photo, "keywords", []) or [],
    }


def get_photos_by_criteria(
    photosdb, keyword=None, location=None, person=None, start_date=None, end_date=None
):
    """Enhanced photo search using QueryOptions"""
    query_params = {
        "photos": True,
        "movies": False,
        "incloud": True,
        "ignore_case": True,
    }

    if keyword:
        query_params["label"] = [keyword]
    if start_date:
        query_params["from_date"] = start_date
    if end_date:
        query_params["to_date"] = end_date

    photos = photosdb.query(QueryOptions(**query_params))

    if location:
        photos = [
            p for p in photos if p.place and fuzzy_match_search(p.place.name, location)
        ]
    if person:
        photos = [
            p
            for p in photos
            if p.persons
            and any(fuzzy_match_search(p_name, person) for p_name in p.persons)
        ]

    return photos


def format_photo_results(photos: List[osxphotos.PhotoInfo], title: str, limit: int = 50) -> str:
    """Standardized photo result formatting with filenames"""
    if not photos:
        return f"No {title.lower()} found"

    photos.sort(key=lambda x: x.date if x.date else datetime.min)

    total = len(photos)
    photos = photos[:limit]

    photo_details = []
    for photo in photos:
        details = get_photo_details(photo)
        photo_details.append(
            f"📷 File: {details['filename']}\n"
            f"   Date: {details['date']}\n"
            f"   Location: {details['location']}\n"
            f"   People: {', '.join(details['persons']) if details['persons'] else 'None'}\n"
            f"   Labels: {', '.join(details['labels']) if details['labels'] else 'None'}\n"
            "---"
        )

    result = [f"{title} ({total} found, showing {len(photos)}):", "", *photo_details]
    return "\n".join(result)


@mcp.tool()
def location_search(location: str) -> str:
    """Find photos from specific locations.

    Args:
        location: Location name to search for (e.g., "Shenzhen", "Berlin", "New York")
    """
    try:
        logger.info(f"Location search for: {location}")
        location_lower = location.lower()
        photos = photos_loader.db.photos()

        matching_photos = [
            photo
            for photo in photos
            if photo.place and photo.place.name and location_lower in photo.place.name.lower()
        ]

        if matching_photos:
            matching_photos.sort(key=lambda x: x.date if x.date else datetime.min)

            total = len(matching_photos)
            display_photos = matching_photos[:50]

            filenames = []
            for photo in display_photos:
                details = get_photo_details(photo)
                filenames.append(
                    f"📷 {details['filename']}\n"
                    f"   Taken: {details['date']}\n"
                    f"   At: {details['location']}\n"
                    "---"
                )

            response = [
                f"Found {total} photos from {location} (showing {len(display_photos)}):",
                "",
                *filenames,
            ]

            return "\n".join(response)
        else:
            return f"No photos found from {location}"

    except Exception as e:
        logger.error(f"Error in location search: {e}", exc_info=True)
        return f"Error searching for location: {str(e)}"


@mcp.tool()
def label_search(label: str) -> str:
    """Search photos by labels or keywords (e.g., Birthday, Beach, Dogs, Food).

    Args:
        label: Label or keyword to search for
    """
    try:
        logger.info(f"Label search for: {label}")
        photos = get_photos_by_criteria(photos_loader.db, keyword=label.lower())
        return format_photo_results(photos, f"Photos labeled as '{label}'")
    except Exception as e:
        logger.error(f"Error in label search: {e}", exc_info=True)
        return f"Error searching for label: {str(e)}"


@mcp.tool()
def people_search(person: str) -> str:
    """Find photos containing specific people.

    Args:
        person: Name of person to search for
    """
    try:
        logger.info(f"People search for: {person}")
        photos = get_photos_by_criteria(photos_loader.db, person=person.lower())
        return format_photo_results(photos, f"Photos with {person}")
    except Exception as e:
        logger.error(f"Error in people search: {e}", exc_info=True)
        return f"Error searching for person: {str(e)}"


@mcp.tool()
def photo_stats() -> str:
    """Get statistics about your photo library including total count, top locations, and people."""
    try:
        logger.info("Getting photo stats")
        photos = photos_loader.db.photos()

        locations = Counter(p.place.name for p in photos if p.place)
        people = Counter(person for p in photos for person in (p.persons or []))
        years = Counter(p.date.year for p in photos if p.date)

        stats = [
            f"📸 Photo Library Statistics",
            f"Total Photos: {len(photos)}",
            "",
            "📍 Top 10 Locations:",
            *[f"  • {loc}: {count} photos" for loc, count in locations.most_common(10)],
            "",
            "👥 Top 10 People:",
            *[f"  • {person}: {count} photos" for person, count in people.most_common(10)],
            "",
            "📅 Photos by Year:",
            *[f"  • {year}: {count} photos" for year, count in sorted(years.items(), reverse=True)[:10]],
        ]

        return "\n".join(stats)
    except Exception as e:
        logger.error(f"Error getting photo stats: {e}", exc_info=True)
        return f"Error getting photo stats: {str(e)}"


if __name__ == "__main__":
    logger.info("Starting Smart Photo Journal MCP server.")
    mcp.run()
