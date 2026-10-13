"""Run the opt-in v3 command interface without importing the legacy bus."""

from .cli import main

raise SystemExit(main())
