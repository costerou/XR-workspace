"""Print the "Play in VITURE" bookmark URL (javascript:...) built from bookmarklet.js (docs/delightxr.md).

usage: python -m tools.delightxr.build
Make a new bookmark in Chromium (Ctrl+Shift+O, "Add new bookmark"), name it "Play in VITURE", and paste this as its URL.
Delete the bookmark to remove it. Nothing else is installed.
"""
from pathlib import Path
import re
import sys
from urllib.parse import quote

SOURCE = Path(__file__).with_name('bookmarklet.js')


def code(source=None):
    """The bookmarklet as one line: block comments dropped, each line trimmed and joined with a space."""
    text = (source if source is not None else SOURCE.read_text(encoding='utf-8'))
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return ' '.join(line.strip() for line in text.splitlines() if line.strip())


def bookmark_url(source=None):
    return 'javascript:' + quote(code(source), safe="()'!*,;:=/?&@+-_.~")  # '#', '%' and spaces encoded


def main(argv=None):
    sys.stdout.write(bookmark_url() + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
