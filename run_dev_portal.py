"""PyInstaller entry point; bare launch opens native project sign-in."""
from dev_portal.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
