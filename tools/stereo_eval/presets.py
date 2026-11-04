"""The presets: one JSON file, read here, by the tests and by the doc check."""
import json
from pathlib import Path

PATH = Path(__file__).with_name('presets.json')
CONTENT = ('desktop', 'video', 'games')


def load() -> dict:
    return {k: v for k, v in json.loads(PATH.read_text()).items() if not k.startswith('_')}


def get(name: str, **changes) -> dict:
    return {**load()[name], **changes}
