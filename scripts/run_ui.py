"""Launch the Streamlit UI."""
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    app = Path(__file__).resolve().parents[1] / "src" / "pitchs_edge" / "ui" / "app.py"
    sys.exit(subprocess.call(["streamlit", "run", str(app)]))
