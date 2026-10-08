"""Thin launcher: ``python setup.py <command>`` (see ``setup/cli.py``).

The package directory ``setup/`` and this file share a name. That is safe:
when run as a script this file is ``__main__`` (not ``setup``), so
``import setup`` resolves to the package directory. The ``__main__`` guard is
required for multiprocessing's spawn start method on Windows.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from setup.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
