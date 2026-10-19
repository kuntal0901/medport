"""Entry point for the packaged Windows app (PyInstaller can't start a package with -m)."""
from medport.__main__ import main

if __name__ == "__main__":
    main()
