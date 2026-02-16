# verkehrsschilder-anki

Automatically generates an Anki flashcard deck for German traffic signs (Verkehrszeichen).

Combines structured data from the [OSM traffic sign tool](https://github.com/osmberlin/osm-traffic-sign-tool) with driving-focused descriptions from [click-learn.de](https://www.click-learn.de/verkehrszeichen/).

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

Output: `output/verkehrszeichen.apkg` — import this file into Anki.

## Options

```
-o, --output PATH   Custom output path (default: output/verkehrszeichen.apkg)
-v, --verbose        Enable debug logging
```

## License

MIT
