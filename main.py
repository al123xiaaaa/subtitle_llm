import sys
from pathlib import Path

project_src = Path(__file__).resolve().parent / "src"
if str(project_src) not in sys.path:
    sys.path.insert(0, str(project_src))

def main() -> None:
    from subtitle_llm.cli.app import app

    app()


if __name__ == "__main__":
    main()
