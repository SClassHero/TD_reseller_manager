"""Local desktop launcher for packaged Windows builds.

This entry point keeps user data beside the executable in a `data/` folder,
opens the browser automatically, and binds only to localhost.
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path


def _runtime_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _runtime_root()
DATA_DIR = ROOT / 'data'

os.environ.setdefault('INVENTORY_DB', str(DATA_DIR / 'inventory.db'))
os.environ.setdefault('INVENTORY_BACKUPS_DIR', str(DATA_DIR / 'backups'))
os.environ.setdefault('INVENTORY_UPLOADS_DIR', str(DATA_DIR / 'uploads' / 'products'))
os.environ.setdefault('INVENTORY_SECRET_KEY_FILE', str(DATA_DIR / '.secret_key'))

for path in (
    DATA_DIR,
    DATA_DIR / 'backups',
    DATA_DIR / 'uploads' / 'products',
):
    path.mkdir(parents=True, exist_ok=True)

from app import app  # noqa: E402


if __name__ == '__main__':
    url = 'http://127.0.0.1:5000'
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
