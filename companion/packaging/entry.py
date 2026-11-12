"""PyInstaller 入口：等价于 `python -m pcmond`（支持 --once / --gui / 无参数 GUI）。"""

import sys

from pcmond.main import main

if __name__ == "__main__":
    sys.exit(main())
