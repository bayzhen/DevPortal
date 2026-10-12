"""PyInstaller entry point; packaged CLI still requires owner-local configuration."""
from dev_portal.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
