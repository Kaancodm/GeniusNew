"""Direct CLI entry point; never imported by the GeniusNew Core."""

from .pilot import main

if __name__ == "__main__":
    raise SystemExit(main())
