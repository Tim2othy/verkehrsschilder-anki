#!/usr/bin/env python3
"""
German Traffic Signs Anki Deck Generator

Combines structured data from the OSM traffic sign tool with rich descriptions
from click-learn.de to produce a comprehensive Anki flashcard deck.
"""

import argparse
import html
import io
import json
import logging
import re
import time
from pathlib import Path

import cairosvg
import genanki
import requests
from bs4 import BeautifulSoup
from PIL import Image

# --- Configuration ---

SOURCE_URL = "https://www.click-learn.de/verkehrszeichen/"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/osmberlin/osm-traffic-sign-tool/main"
OSM_DATA_DIR = "packages/traffic-sign-converter/src/data-definitions/DE"
OSM_SVG_DIR = "packages/internal_svgs/src/data-svgs/DE/svgs"
DEGENER_IMAGE_BASE = "https://www.degener.de/fileadmin/medien/verkehrszeichen"

# Deterministic IDs for reproducibility (generated once, never change)
DECK_ID = 2024_02_15_001
MODEL_ID = 2024_02_15_002

DATA_FILES = [
    "infrastructure",
    "traffic_ban",
    "speed_zones",
    "speed_maxspeed_start",
    "speed_maxspeed_end",
    "speed_minspeed_start",
    "speed_minspeed_end",
    "overtaking",
    "notice",
    "exceptions__thing_allowed",
    "conditions__only_for_thing",
    "conditions__time",
    "conditions__other",
    "surface",
    "hazard",
    "numbers",
]

log = logging.getLogger(__name__)


# --- Phase A: Extract OSM data ---


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def fetch_url(
    url: str, retries: int = 3, delay: float = 1.0, raise_on_404: bool = True
) -> requests.Response | None:
    """Fetch URL with retries and exponential backoff.

    Only retries on network/timeout errors, not on HTTP 4xx errors.
    If raise_on_404=False, returns None on 404 instead of raising.
    """
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30, headers=HEADERS)
            if resp.status_code == 404 and not raise_on_404:
                return None
            resp.raise_for_status()
            return resp
        except requests.ConnectionError as e:
            if attempt == retries - 1:
                raise
            log.warning("Retry %d for %s: %s", attempt + 1, url, e)
            time.sleep(delay * (2**attempt))
        except requests.RequestException:
            raise


def parse_ts_signs(ts_content: str) -> list[dict]:
    """Parse TypeScript sign definitions into dicts.

    Extracts key fields using regex — the TS files are essentially object literals.
    """
    signs = []

    # Match each object block in the array
    # We look for signId as anchor and extract fields around it
    sign_blocks = re.split(r"\n  \{", ts_content)

    for block in sign_blocks:
        sign_id_m = re.search(r"signId:\s*'([^']+)'", block)
        if not sign_id_m:
            continue

        sign_id = sign_id_m.group(1)

        name_m = re.search(r"\bname:\s*'([^']+)'", block)
        desc_name_m = re.search(r"descriptiveName:\s*'([^']*)'", block)
        # description can be null or a string
        desc_m = re.search(r"\bdescription:\s*'([^']*)'", block)
        kind_m = re.search(r"kind:\s*'([^']+)'", block)
        category_m = re.search(r"signCategory:\s*'([^']+)'", block)
        source_url_m = re.search(r"sourceUrl:\s*\n?\s*'([^']+)'", block)
        if not source_url_m:
            source_url_m = re.search(r"sourceUrl:\s*'([^']+)'", block)
        osm_value_m = re.search(r"osmValuePart:\s*'([^']+)'", block)

        signs.append(
            {
                "sign_id": sign_id,
                "osm_value": osm_value_m.group(1) if osm_value_m else sign_id,
                "name": name_m.group(1) if name_m else f"Zeichen {sign_id}",
                "descriptive_name": desc_name_m.group(1) if desc_name_m else "",
                "description": desc_m.group(1) if desc_m else "",
                "kind": kind_m.group(1) if kind_m else "traffic_sign",
                "category": category_m.group(1) if category_m else "traffic_sign",
                "image_source_url": source_url_m.group(1) if source_url_m else "",
            }
        )

    return signs


def fetch_osm_data() -> list[dict]:
    """Fetch and parse all OSM traffic sign data files."""
    all_signs = []

    for filename in DATA_FILES:
        url = f"{GITHUB_RAW_BASE}/{OSM_DATA_DIR}/data/{filename}.ts"
        log.info("Fetching OSM data: %s", filename)
        try:
            resp = fetch_url(url)
            assert resp is not None
            signs = parse_ts_signs(resp.text)
            log.info("  -> %d signs from %s", len(signs), filename)
            all_signs.extend(signs)
        except requests.RequestException as e:
            log.warning("Failed to fetch %s: %s", filename, e)

    # Also fetch the main file for inline misc signs
    url = f"{GITHUB_RAW_BASE}/{OSM_DATA_DIR}/trafficSignDataDE.ts"
    log.info("Fetching OSM data: trafficSignDataDE.ts (misc signs)")
    try:
        resp = fetch_url(url)
        assert resp is not None
        misc_signs = parse_ts_signs(resp.text)
        # Filter out signs we already have
        existing_ids = {s["sign_id"] for s in all_signs}
        new_misc = [s for s in misc_signs if s["sign_id"] not in existing_ids]
        log.info("  -> %d new misc signs", len(new_misc))
        all_signs.extend(new_misc)
    except requests.RequestException as e:
        log.warning("Failed to fetch main data file: %s", e)

    log.info("Total OSM signs: %d", len(all_signs))
    return all_signs


def sign_id_to_svg_filename(sign_id: str) -> str:
    """Convert sign ID to the SVG filename used in the OSM repo.

    Rules: hyphens -> _, dots -> _, brackets [X] -> __X__
    """
    name = sign_id
    # Handle bracket values: [5.5] -> __5.5__
    name = re.sub(r"\[([^\]]+)\]", r"__\1__", name)
    name = name.replace("-", "_").replace(".", "_")
    return f"DE_{name}.svg"


def download_svg(sign_id: str) -> bytes | None:
    """Download SVG from the OSM repo."""
    filename = sign_id_to_svg_filename(sign_id)
    url = f"{GITHUB_RAW_BASE}/{OSM_SVG_DIR}/{filename}"
    resp = fetch_url(url, raise_on_404=False)
    if resp is None:
        log.debug("SVG not found in OSM repo: %s", filename)
        return None
    return resp.content


def svg_to_png(svg_data: bytes, width: int = 400) -> bytes:
    """Convert SVG to PNG using cairosvg."""
    result = cairosvg.svg2png(bytestring=svg_data, output_width=width)
    assert isinstance(result, bytes)
    return result


def download_degener_png(sign_number: str) -> bytes | None:
    """Download PNG fallback from degener.de."""
    url = f"{DEGENER_IMAGE_BASE}/{sign_number}.png"
    try:
        resp = fetch_url(url, raise_on_404=False)
        if resp is None:
            return None
        # Validate it's actually an image
        img = Image.open(io.BytesIO(resp.content))
        img.verify()
        return resp.content
    except Exception:
        log.debug("Degener PNG not found: %s", sign_number)
        return None


# --- Phase B: Scrape click-learn.de ---


def scrape_click_learn() -> dict[str, dict]:
    """Scrape click-learn.de for sign descriptions.

    Returns dict mapping sign_number -> {name, description, number}.
    """
    log.info("Scraping click-learn.de...")
    resp = fetch_url(SOURCE_URL)
    assert resp is not None
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    signs = {}
    table = soup.find("table")
    if not table:
        log.warning("No table found on click-learn.de")
        return signs

    rows = table.find_all("tr")
    log.info("Found %d table rows", len(rows))

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Column 0: sign number
        number_text = cells[0].get_text(strip=True)
        if not number_text or not number_text[0].isdigit():
            continue

        # Column 1: name + description
        # The first line or bold text is typically the name
        name_tag = cells[1].find(["strong", "b"])
        name = name_tag.get_text(strip=True) if name_tag else ""

        # Full text as description
        full_text = cells[1].get_text("\n", strip=True)
        # Remove the name from the beginning of description if present
        description = full_text
        if name and description.startswith(name):
            description = description[len(name) :].strip()
            # Remove leading newline/dash
            description = description.lstrip("\n-– ")

        signs[number_text] = {
            "number": number_text,
            "name": name,
            "description": description,
        }

    log.info("Scraped %d signs from click-learn.de", len(signs))
    return signs


STRASSENAUSSTATTER_SEARCH = "https://www.strassenausstatter.de/?s=verkehrszeichen+{}"


def fetch_strassenausstatter_description(sign_id: str) -> str:
    """Try to fetch a description from strassenausstatter.de search results."""
    url = STRASSENAUSSTATTER_SEARCH.format(sign_id)
    resp = fetch_url(url, raise_on_404=False)
    if resp is None:
        return ""
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    # Look for "Bedeutung:" in the page text
    for el in soup.find_all(["p", "div", "span"]):
        text = el.get_text()
        if "Bedeutung:" in text:
            # Extract the text after "Bedeutung:"
            idx = text.index("Bedeutung:")
            desc = text[idx + len("Bedeutung:") :].strip()
            # Clean up — take until the next section heading or end
            # Often followed by "Aufstellung:" or similar
            for cutoff in ["Aufstellung:", "Grundlage:", "Besonderheit:"]:
                if cutoff in desc:
                    desc = desc[: desc.index(cutoff)].strip()
            if desc:
                return desc
    return ""


def fill_missing_descriptions(signs: list[dict]) -> None:
    """Fill in missing descriptions from strassenausstatter.de."""
    missing = [s for s in signs if not s["description"] and s["sign_id"][0].isdigit()]
    if not missing:
        return

    log.info("Fetching descriptions for %d signs from strassenausstatter.de...", len(missing))
    filled = 0
    for i, sign in enumerate(missing, 1):
        if i % 10 == 0:
            log.info("  Description lookup: %d/%d", i, len(missing))
        desc = fetch_strassenausstatter_description(sign["sign_id"])
        if desc:
            sign["description"] = desc
            filled += 1
        time.sleep(0.2)  # Be polite

    log.info("Filled %d/%d missing descriptions", filled, len(missing))


# --- Phase C: Merge data ---


def get_category_tag(sign_id: str, osm_category: str) -> str:
    """Map sign to a German category tag for Anki."""
    # Use OSM category if available
    category_map = {
        "traffic_sign": "",  # will use number-based fallback
        "hazard_sign": "gefahrzeichen",
        "speed": "geschwindigkeit",
        "exception_modifier": "zusatzzeichen",
        "condition_modifier": "zusatzzeichen",
        "object_sign": "objektzeichen",
        "surface_sign": "strassenzeichen",
        "signpost": "wegweiser",
    }

    tag = category_map.get(osm_category, "")
    if tag:
        return tag

    # Fallback: categorize by number range
    m = re.match(r"(\d+)", sign_id)
    if not m:
        return "sonstige"
    try:
        base_num = int(m.group(1))
    except ValueError:
        return "sonstige"

    if 100 <= base_num < 200:
        return "gefahrzeichen"
    elif 200 <= base_num < 300:
        return "vorschriftzeichen"
    elif 300 <= base_num < 400:
        return "richtzeichen"
    elif 400 <= base_num < 600:
        return "wegweisung"
    elif 600 <= base_num < 800:
        return "verkehrseinrichtungen"
    elif base_num >= 1000:
        return "zusatzzeichen"
    return "sonstige"


def merge_signs(
    osm_signs: list[dict], click_learn: dict[str, dict]
) -> list[dict]:
    """Merge OSM structured data with click-learn.de descriptions."""
    merged = []
    osm_ids_used = set()

    for sign in osm_signs:
        sid = sign["sign_id"]
        osm_ids_used.add(sid)

        # Look up click-learn data by sign_id
        cl = click_learn.get(sid, {})

        # Prefer click-learn description (richer), fall back to OSM
        if cl.get("description"):
            description = cl["description"]
        elif sign.get("description"):
            description = sign["description"]
        else:
            description = ""

        # Use click-learn name if it exists and is more descriptive
        cl_name = cl.get("name", "")
        osm_name = sign.get("descriptive_name", "")
        display_name = cl_name if cl_name else osm_name

        category_tag = get_category_tag(sid, sign.get("category", ""))

        merged.append(
            {
                "sign_id": sid,
                "name": sign.get("name", f"Zeichen {sid}"),
                "display_name": display_name,
                "description": description,
                "category_tag": category_tag,
                "source": "osm",
            }
        )

    # Add signs that are only on click-learn.de
    for number, cl in click_learn.items():
        if number not in osm_ids_used:
            category_tag = get_category_tag(number, "")
            merged.append(
                {
                    "sign_id": number,
                    "name": f"Zeichen {number}",
                    "display_name": cl.get("name", ""),
                    "description": cl.get("description", ""),
                    "category_tag": category_tag,
                    "source": "click_learn",
                }
            )

    log.info(
        "Merged: %d total signs (%d from OSM, %d click-learn only)",
        len(merged),
        len(osm_ids_used),
        len(merged) - len(osm_ids_used),
    )
    return merged


# --- Phase D: Download images ---


def download_all_images(signs: list[dict]) -> dict[str, bytes]:
    """Download images for all signs. Try OSM SVG first, then degener.de PNG."""
    images = {}
    total = len(signs)

    for i, sign in enumerate(signs, 1):
        sid = sign["sign_id"]
        if i % 20 == 0 or i == total:
            log.info("Downloading images: %d/%d", i, total)

        # Try OSM SVG first
        svg_data = download_svg(sid)
        if svg_data:
            try:
                png_data = svg_to_png(svg_data)
                images[sid] = png_data
                continue
            except Exception as e:
                log.warning("SVG conversion failed for %s: %s", sid, e)

        # Fallback: degener.de PNG
        png_data = download_degener_png(sid)
        if png_data:
            images[sid] = png_data
            continue

        log.warning("No image found for sign %s", sid)
        # Small delay to be polite
        time.sleep(0.05)

    log.info("Downloaded %d/%d images", len(images), total)
    return images


# --- Phase E: Build Anki deck ---

CARD_CSS = """\
.card {
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  font-size: 18px;
  text-align: center;
  color: #333;
  background-color: #f5f5f5;
  padding: 20px;
  max-width: 600px;
  margin: 0 auto;
}
.image-box {
  background: white;
  padding: 20px;
  border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  display: inline-block;
  margin-bottom: 16px;
}
.image-box img {
  max-width: 280px;
  max-height: 280px;
}
.sign-number {
  font-size: 13px;
  color: #888;
  font-weight: bold;
  margin-bottom: 4px;
}
.sign-name {
  font-size: 22px;
  color: #2c3e50;
  font-weight: 600;
  margin: 8px 0;
}
.description {
  font-size: 15px;
  line-height: 1.5;
  color: #555;
  text-align: left;
  background: white;
  padding: 12px 16px;
  border-radius: 8px;
  border-left: 4px solid #3498db;
  margin-top: 12px;
}
.hint {
  color: #999;
  font-size: 14px;
  font-style: italic;
  margin-top: 12px;
}
"""

FRONT_TEMPLATE = """\
<div class="card">
  <div class="image-box">{{Image}}</div>
  <div class="hint">Welches Verkehrszeichen ist das?</div>
</div>
"""

BACK_TEMPLATE = """\
<div class="card">
  <div class="image-box">{{Image}}</div>
  <div class="sign-number">{{Number}}</div>
  <div class="sign-name">{{Name}}</div>
  {{#Description}}
  <div class="description">{{Description}}</div>
  {{/Description}}
</div>
"""


def create_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "Deutsche Verkehrszeichen",
        fields=[
            {"name": "Image"},
            {"name": "Number"},
            {"name": "Name"},
            {"name": "Description"},
        ],
        templates=[
            {
                "name": "Bild → Info",
                "qfmt": FRONT_TEMPLATE,
                "afmt": BACK_TEMPLATE,
            },
        ],
        css=CARD_CSS,
    )


def build_deck(
    signs: list[dict], images: dict[str, bytes], output_path: str
) -> Path:
    """Build the Anki .apkg deck file."""
    model = create_model()
    deck = genanki.Deck(DECK_ID, "Deutsche Verkehrszeichen")

    media_files = []
    cards_created = 0
    skipped = 0

    for sign in signs:
        sid = sign["sign_id"]
        png_data = images.get(sid)

        if not png_data:
            skipped += 1
            continue

        # Create image filename for Anki media
        img_filename = f"verkehrszeichen_{sid.replace('[', '_').replace(']', '_')}.png"

        # Write temp image file for packaging
        tmp_path = Path("output") / "media" / img_filename
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(png_data)
        media_files.append(str(tmp_path))

        # Build note
        image_field = f'<img src="{img_filename}">'
        number_field = html.escape(sign["name"])
        name_field = html.escape(sign["display_name"])
        desc_field = html.escape(sign["description"]).replace("\n", "<br>")

        if sign["category_tag"]:
            tags = [f"verkehrszeichen::{sign['category_tag']}"]
        else:
            tags = ["verkehrszeichen"]

        note = genanki.Note(
            model=model,
            fields=[image_field, number_field, name_field, desc_field],
            tags=tags,
        )
        deck.add_note(note)
        cards_created += 1

    # Package
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    package = genanki.Package(deck)
    package.media_files = media_files
    package.write_to_file(str(out))

    log.info(
        "Deck created: %d cards, %d skipped (no image), file: %s",
        cards_created,
        skipped,
        out,
    )
    return out


# --- Phase F: Verify ---


def verify_deck(deck_path: Path):
    """Basic verification of the generated deck."""
    import zipfile

    if not deck_path.exists():
        log.error("Deck file not found: %s", deck_path)
        return False

    size_mb = deck_path.stat().st_size / (1024 * 1024)
    log.info("Deck size: %.1f MB", size_mb)

    try:
        with zipfile.ZipFile(deck_path, "r") as z:
            names = z.namelist()
            media_count = 0
            if "media" in names:
                media_json = json.loads(z.read("media"))
                media_count = len(media_json)
            log.info("Media files in deck: %d", media_count)
    except zipfile.BadZipFile:
        log.error("Invalid ZIP/APKG file!")
        return False

    log.info("Verification passed.")
    return True


# --- Main ---


def main():
    parser = argparse.ArgumentParser(
        description="Generate Anki deck for German traffic signs"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output/verkehrszeichen.apkg",
        help="Output .apkg file path",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("=== German Traffic Signs Anki Deck Generator ===")

    # Phase A: OSM data
    log.info("--- Phase A: Fetching OSM sign data ---")
    osm_signs = fetch_osm_data()

    # Phase B: click-learn.de descriptions
    log.info("--- Phase B: Scraping click-learn.de ---")
    click_learn_data = scrape_click_learn()

    # Phase C: Merge
    log.info("--- Phase C: Merging data ---")
    merged_signs = merge_signs(osm_signs, click_learn_data)

    # Phase C2: Fill missing descriptions from strassenausstatter.de
    log.info("--- Phase C2: Filling missing descriptions ---")
    fill_missing_descriptions(merged_signs)

    # Phase D: Download images
    log.info("--- Phase D: Downloading images ---")
    images = download_all_images(merged_signs)

    # Phase E: Build deck
    log.info("--- Phase E: Building Anki deck ---")
    deck_path = build_deck(merged_signs, images, args.output)

    # Phase F: Verify
    log.info("--- Phase F: Verifying deck ---")
    verify_deck(deck_path)

    log.info("=== Done! Import %s into Anki. ===", deck_path)


if __name__ == "__main__":
    main()
