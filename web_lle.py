from pathlib import Path
import runpy
import sys


def main() -> None:
    lle_dir = Path(__file__).resolve().parent / "lle"
    app_path = lle_dir / "app.py"
    sys.path.insert(0, str(lle_dir))
    runpy.run_path(str(app_path), run_name="__main__")


if __name__ == "__main__":
    main()
