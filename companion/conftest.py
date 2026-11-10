# 让 `python -m pytest companion -q`（在仓库根目录）也能 import pcmond，
# 不依赖 rootdir 推断或安装成包。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
