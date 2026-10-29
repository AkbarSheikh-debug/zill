"""Concept: `python -m zill` is the front door; all logic lives in zill.client."""
import sys

from zill.client import main

sys.exit(main())
