import hashlib
from pathlib import Path


def sidecar_path(output_file: str | Path, suffix: str) -> Path:
    output_path = Path(output_file)
    return output_path.with_name(f"{output_path.stem}{suffix}")


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
