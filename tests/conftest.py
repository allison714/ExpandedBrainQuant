import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))          # so tests can import synthetic.py
sys.path.insert(0, str(Path(__file__).parent.parent))   # and the package without installing it
