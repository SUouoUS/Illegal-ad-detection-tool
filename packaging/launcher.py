"""Entry point of the Windows executable (ClearWeb.exe).

No arguments: local screen on 127.0.0.1 and the default browser opens.
`ClearWeb.exe scan <url> [--ocr] ...`: scan without the screen.
"""
import multiprocessing
import sys

from clearweb.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
