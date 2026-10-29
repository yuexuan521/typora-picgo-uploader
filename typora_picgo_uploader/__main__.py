"""支持 ``python -m typora_picgo_uploader``。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
