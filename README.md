# verkehrsschilder-anki

Automatically generates an Anki flashcard deck for German traffic signs (Verkehrszeichen).

~340 cards with images, sign numbers, names, and driving-relevant descriptions. Cards are tagged by category for filtered studying.

## Data Sources

- **[OSM traffic sign tool](https://github.com/osmberlin/osm-traffic-sign-tool)** — structured sign data + public domain SVG images (~199 signs)
- **[click-learn.de](https://www.click-learn.de/verkehrszeichen/)** — practical driving descriptions (~259 signs)
- **[strassenausstatter.de](https://www.strassenausstatter.de/)** — fallback descriptions for signs not covered above
- **[degener.de](https://www.degener.de/)** — PNG image fallback

## Card Format

- **Front:** Traffic sign image
- **Back:** Sign number, name, and description

Tags use Anki's hierarchical format: `verkehrszeichen::gefahrzeichen`, `verkehrszeichen::vorschriftzeichen`, etc.

## Setup

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Note: `cairosvg` requires Cairo to be installed on your system. See [cairosvg docs](https://cairosvg.org/documentation/#installation) for platform-specific instructions.

## Usage

```bash
python generate_deck.py
```

Output: `output/verkehrszeichen.apkg` — import this file into Anki via File > Import.

### Options

```
-o, --output PATH   Custom output path (default: output/verkehrszeichen.apkg)
-v, --verbose        Enable debug logging
```

## License

MIT
