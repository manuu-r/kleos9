"""Make shared test helpers importable from nested test packages such as ``tests/scenarios``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
