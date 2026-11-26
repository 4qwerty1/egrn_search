import sys
from pathlib import Path

from egrn.main import run


if __name__ == "__main__":
    sys.exit(run(Path("data")))
