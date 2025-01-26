import logging
import re
import threading
from collections import Counter
from typing import List, Optional, Dict
import osxphotos
from datetime import datetime, timedelta, timezone
from mcp.server import Server
from mcp.server.models import InitializationOptions, ServerCapabilities
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from dateparser import parse
from osxphotos import QueryOptions
from thefuzz import fuzz

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("smart_photo_journal.log"),
        logging.StreamHandler(),
    ],
)


class PhotosDBLoader:
    def __init__(self):
        self._db: Optional[osxphotos.PhotosDB] = None
        self.start_loading()

    def start_loading(self):
        def load():
            try:
                self._db = osxphotos.PhotosDB()
                logging.info("PhotosDB loaded successfully.")
            except Exception as e:
                logging.error(f"Failed to load PhotosDB: {e}")
                raise

        thread = threading.Thread(target=load)
        thread.daemon = True
        thread.start()

    @property
    def db(self) -> osxphotos.PhotosDB:
        if self._db is None:
            logging.warning("PhotosDB is still loading; access attempted.")
            raise Exception("PhotosDB is still loading. Please try again later.")
        return self._db


# Global PhotosDB loader instance
photos_loader = PhotosDBLoader()

# Initialize MCP server
server = Server("smart-photo-journal")


def fuzzy_match_search(text: str, search_term: str, threshold=60) -> bool:
    """Use fuzzy matching for more flexible text searching"""
    if not text or not search_term:
        return False
    return fuzz.ratio(text.lower(), search_term.lower()) > threshold


def get_photo_details(photo: osxphotos.PhotoInfo) -> dict:
    """Enhanced photo details with reliable filename extraction"""
    # Extract filename using multiple fallback methods
    filename = (
        getattr(photo, "original_filename", None)
        or getattr(photo, "title", None)
        or photo.path.split("/")[-1]
        or photo.filename
    )

    return {
        "filename": filename,
        "date": photo.date.strftime("%Y-%m-%d %H:%M:%S"),
        "location": photo.place.name if photo.place else "Unknown",
        "path": photo.path,
        "persons": photo.persons,
        "labels": getattr(photo, "labels", []),
        "keywords": getattr(photo, "keywords", []),
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

    # Get initial results
    photos = photosdb.query(QueryOptions(**query_params))

    # Apply additional filters that aren't supported by QueryOptions
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


def parse_date_range(query: str):
    """Parse natural language date queries"""
    parsed_date = parse(query, settings={"PREFER_DATES_FROM": "future"})

    if not parsed_date:
        raise ValueError("Could not parse date from query")

    start_date = parsed_date.replace(day=1)
    end_date = (start_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    return start_date, end_date


def format_photo_results(
    photos: List[osxphotos.PhotoInfo], title: str
) -> List[TextContent]:
    """Standardized photo result formatting with filenames"""
    if not photos:
        return [TextContent(type="text", text=f"No {title.lower()} found")]

    photos.sort(key=lambda x: x.date)

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

    response = [f"{title} ({len(photos)} found):", "", *photo_details]

    return [TextContent(type="text", text="\n".join(response))]


def parse_complex_query(query: str) -> dict:
    """Parse complex natural language queries with enhanced pattern matching"""
    params = {}

    # Handle both explicit and natural language patterns
    # Explicit format: location:Udaipur person:papa
    explicit_location = re.search(r"location:(\w+)", query)
    explicit_person = re.search(r"person:(\w+)", query)

    # Natural language: "photos of/from/in Udaipur with papa"
    natural_location = re.search(r"(?:from|in|of)\s+([A-Za-z\s]+?)(?:\s+with|$)", query)
    natural_person = re.search(r"with\s+([A-Za-z\s]+?)(?:\s+in|from|$)", query)

    # Set location parameter
    if explicit_location:
        params["location"] = explicit_location.group(1)
    elif natural_location:
        params["location"] = natural_location.group(1).strip()

    # Set person parameter
    if explicit_person:
        params["person"] = explicit_person.group(1)
    elif natural_person:
        params["person"] = natural_person.group(1).strip()

    return params

@server.call_tool()
async def handle_time_analysis(self, query_input: dict) -> List[TextContent]:
    """Analyze photo patterns with filename display"""
    photos = photos_loader.db.photos()

    hour_distribution = Counter(photo.date.hour for photo in photos)
    weekday_distribution = Counter(photo.date.strftime("%A") for photo in photos)

    analysis = [
        "📸 Photo Taking Patterns:",
        f"Total Photos: {len(photos)}",
        "",
        "⏰ Hourly Distribution:",
    ]

    # Add photo details for each time slot
    for hour, count in sorted(hour_distribution.items()):
        hour_photos = [p for p in photos if p.date.hour == hour]
        analysis.append(f"\n{hour:02d}:00 - {count} photos:")
        for photo in hour_photos[:3]:  # Show first 3 photos per hour
            details = get_photo_details(photo)
            analysis.append(f"  • {details['filename']} ({details['date']})")

    return [TextContent(type="text", text="\n".join(analysis))]


@server.call_tool()
async def handle_complex_search(self, query_input: dict) -> List[TextContent]:
    """Handle complex multi-criteria photo searches"""
    try:
        query = query_input["query"]
        params = parse_complex_query(query)

        # Get photos matching all criteria
        photos = get_photos_by_criteria(
            photos_loader.db,
            location=params.get("location"),
            person=params.get("person"),
        )

        return format_photo_results(
            photos,
            f"Photos from {params.get('location', 'anywhere')} with {params.get('person', 'anyone')}",
        )

    except Exception as e:
        logging.error(f"Error in complex search: {e}")
        return [TextContent(type="text", text=f"Error processing search: {str(e)}")]


@server.call_tool()
async def handle_label_search(self, query_input: dict) -> List[TextContent]:
    """Enhanced label search with filename display"""
    label = query_input["label"].lower()
    photos = get_photos_by_criteria(photos_loader.db, keyword=label)
    return format_photo_results(photos, f"Photos labeled as '{label}'")


@server.call_tool()
async def handle_people_search(self, query_input: dict) -> List[TextContent]:
    """Enhanced people search with filename display"""
    person = query_input["person"].lower()
    photos = get_photos_by_criteria(photos_loader.db, person=person)
    return format_photo_results(photos, f"Photos with {person}")


def generate_photo_analysis(photos: List[osxphotos.PhotoInfo], title: str) -> str:
    """Generate comprehensive analysis with filenames"""
    locations = Counter(p.place.name for p in photos if p.place)
    people = Counter(person for p in photos for person in p.persons if p.persons)
    months = Counter(p.date.strftime("%B %Y") for p in photos)

    analysis = [
        f"📸 {title} ({len(photos)} photos)",
        "",
        "📍 Top Locations:",
        *[f"  • {loc}: {count} photos" for loc, count in locations.most_common(5)],
        "",
        "👥 Featured People:",
        *[
            f"  • {person}: {count} appearances"
            for person, count in people.most_common(5)
        ],
        "",
        "📅 Timeline:",
        *[f"  • {month}: {count} photos" for month, count in months.most_common(5)],
        "",
        "🖼️ Photo Details:",
    ]

    for photo in photos:
        details = get_photo_details(photo)
        analysis.append(
            f"\nFile: {details['filename']}\n"
            f"Date: {details['date']}\n"
            f"Location: {details['location']}\n"
            f"People: {', '.join(details['persons']) if details['persons'] else 'None'}"
        )

    return "\n".join(analysis)


@server.call_tool()
async def handle_location_search(self, query_input: dict) -> List[TextContent]:
    """Location-based photo search with original filenames"""
    try:
        location = query_input["location"].lower()
        photos = photos_loader.db.photos()

        # Filter photos by location
        matching_photos = [
            photo
            for photo in photos
            if photo.place and location in photo.place.name.lower()
        ]

        if matching_photos:
            # Sort by date for chronological order
            matching_photos.sort(key=lambda x: x.date)

            # Build response focusing on filenames
            filenames = []
            for photo in matching_photos:
                details = get_photo_details(photo)
                filenames.append(
                    f"📷 {details['filename']}\n"
                    f"   Taken: {details['date']}\n"
                    f"   At: {details['location']}\n"
                    "---"
                )

            response = [
                f"Found {len(matching_photos)} photos from {query_input['location']}:",
                "",
                *filenames,
            ]

            return [TextContent(type="text", text="\n".join(response))]
        else:
            return [
                TextContent(
                    type="text", text=f"No photos found from {query_input['location']}"
                )
            ]

    except Exception as e:
        logging.error(f"Error in location search: {e}")
        return [
            TextContent(type="text", text=f"Error searching for location: {str(e)}")
        ]


# List available tools
@server.list_tools()
async def handle_list_tools() -> List[Tool]:
    return [
        Tool(
            name="location-search",
            description="Find photos from specific locations",
            inputSchema={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Location name to search for",
                    }
                },
                "required": ["location"],
            },
        ),
        Tool(
            name="label-search",
            description="Search photos by labels or keywords (e.g., Birthday, Beach, Dogs)",
            inputSchema={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Label or keyword to search for",
                    }
                },
                "required": ["label"],
            },
        ),
        Tool(
            name="people-search",
            description="Find photos containing specific people",
            inputSchema={
                "type": "object",
                "properties": {
                    "person": {
                        "type": "string",
                        "description": "Name of person to search for",
                    }
                },
                "required": ["person"],
            },
        ),
        # TODO: Fix complex search tool, currently disabled; Either better regex or NLP
        # Tool(
        #     name="complex-search",
        #     description="Search photos with multiple criteria (location, people, dates, labels)",
        #     inputSchema={
        #         "type": "object",
        #         "properties": {
        #             "query": {
        #                 "type": "string",
        #                 "description": "Natural language query combining multiple search criteria",
        #             }
        #         },
        #         "required": ["query"],
        #     },
        # ),
    ]


# Main server loop
async def main():
    """Main function to start the MCP server."""
    try:
        logging.info("Starting Smart Photo Journal MCP server.")
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="Smart Photo Journal",
                    server_version="1.0",
                    capabilities=ServerCapabilities(
                        experimental_capabilities={},
                    ),
                ),
            )
    except Exception as e:
        logging.critical(f"Critical error in MCP server: {e}")
        raise


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server shutdown requested by user.")
    except Exception as e:
        logging.critical(f"Unhandled exception during server runtime: {e}")
