"""
Shared setup for the scripts in tests/.

`extractor` creates receipts.db and the scan folders relative to the current
directory when imported, so these scripts always run from a throwaway working
directory. That keeps them away from the real database and scans.
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def use_utf8_output():
    # extractor.py prints emoji; a redirected Windows console (cp1252) would crash on them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def import_extractor(workdir=None):
    """Imports extractor from a scratch directory. Returns (extractor module, workdir)."""
    use_utf8_output()
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="receipt_test_"))
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)
    sys.path.insert(0, str(REPO))
    import extractor
    return extractor, workdir
