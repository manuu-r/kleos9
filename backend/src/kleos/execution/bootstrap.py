"""Trusted standalone image entrypoint; the image carries only this native package."""
import sys

sys.path.insert(0, '/opt/kleos-native')
from kleos.execution.native_runner import main  # noqa: E402

main()
