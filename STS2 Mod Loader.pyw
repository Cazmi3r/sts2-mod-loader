"""Windows launcher: double-click to open STS2 Mod Loader without a console window.
(Also works on Linux and macOS: python3 "STS2 Mod Loader.pyw")"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from sts2mods.__main__ import main  # noqa: E402

sys.exit(main())
