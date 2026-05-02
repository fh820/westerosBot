import tokenize
from io import BytesIO
from pathlib import Path

EXCLUDE_DIRS = {"venv", ".git", "__pycache__", ".idea"}
ROOT = Path(__file__).resolve().parents[1]


def strip_comments(code: str) -> str:
    tokens = tokenize.tokenize(BytesIO(code.encode()).readline)
    kept = [t for t in tokens if t.type != tokenize.COMMENT]
    return tokenize.untokenize(kept).decode()


def process_file(path: Path):
    try:
        original = path.read_text(encoding="utf-8")
        cleaned = strip_comments(original)
        if cleaned != original:
            path.write_text(cleaned, encoding="utf-8")
            print(f"✔ Cleaned {path}")
        else:
            print(f"→ No comments in {path}")
    except Exception as e:
        print(f"✖ Skipped {path}: {e}")


def walk(root: Path):
    for item in root.iterdir():
        if item.is_dir():
            if item.name in EXCLUDE_DIRS:
                return
            walk(item)
        elif item.suffix == ".py":
            process_file(item)


if __name__ == "__main__":
    walk(ROOT)
