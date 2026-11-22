"""Extract selected SQLite files and dictionaries from the official BIRD dev bundle."""

import argparse
import re
import shutil
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import ZipFile


def extract(archive: ZipFile, db_id: str, output: Path) -> None:
    expected = f"{db_id}.sqlite"
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if path.parts[-2:] == (db_id, expected):
            with archive.open(member) as source, (output / expected).open("wb") as target:
                shutil.copyfileobj(source, target)
        elif (
            path.parts[-3:-1] == (db_id, "database_description")
            and re.fullmatch(r"[\w .-]+\.csv", path.name)
            and not path.name.startswith("._")
        ):
            descriptions = output / "database_description"
            descriptions.mkdir(exist_ok=True)
            with archive.open(member) as source, (descriptions / path.name).open("wb") as target:
                shutil.copyfileobj(source, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("db_id")
    parser.add_argument("--db-root", type=Path, default=Path("data/benchmarks/dev_databases"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_]+", args.db_id):
        parser.error("Invalid database ID.")
    output = args.db_root / args.db_id
    if output.exists():
        parser.error(f"Output directory already exists: {output}")
    args.db_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.db_root) as scratch:
        staging = Path(scratch) / args.db_id
        staging.mkdir()
        with ZipFile(args.archive) as outer:
            nested = next(
                (
                    item
                    for item in outer.infolist()
                    if PurePosixPath(item.filename).name == "dev_databases.zip"
                ),
                None,
            )
            if nested is None:
                extract(outer, args.db_id, staging)
            else:
                path = Path(scratch) / "nested.zip"
                with outer.open(nested) as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                with ZipFile(path) as databases:
                    extract(databases, args.db_id, staging)
        if not (staging / f"{args.db_id}.sqlite").is_file():
            parser.error("No matching SQLite database in the archive.")
        staging.rename(output)
    print(output / f"{args.db_id}.sqlite")


if __name__ == "__main__":
    main()
