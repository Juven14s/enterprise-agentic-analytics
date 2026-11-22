"""Load UCI Online Retail into the project's Compose database using the owner connection."""

import argparse
import csv
import os
import shutil
import subprocess
from hashlib import sha256
from io import BytesIO, TextIOWrapper
from pathlib import Path
from tempfile import TemporaryFile
from zipfile import ZipFile

from openpyxl import load_workbook

WORKBOOK_SHA256 = "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"
LINE_COUNT = 541_909
START_IMPORT = """
BEGIN;
CREATE TEMP TABLE retail_import (
    invoice_no text NOT NULL,
    invoice_date timestamp NOT NULL,
    country text NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric NOT NULL
) ON COMMIT DROP;
COPY retail_import FROM STDIN WITH (FORMAT csv);
"""
FINISH_IMPORT = r"""\.
CREATE SCHEMA IF NOT EXISTS online_retail;
CREATE TABLE IF NOT EXISTS online_retail.invoice_lines (LIKE retail_import INCLUDING ALL);
TRUNCATE online_retail.invoice_lines;
INSERT INTO online_retail.invoice_lines SELECT * FROM retail_import;
CREATE INDEX IF NOT EXISTS invoice_lines_country_date
    ON online_retail.invoice_lines (country, invoice_date);
ANALYZE online_retail.invoice_lines;
GRANT USAGE ON SCHEMA online_retail TO analytics_reader;
GRANT SELECT ON online_retail.invoice_lines TO analytics_reader;
COMMIT;
"""


def write_import(archive: Path, output: TextIOWrapper) -> None:
    with ZipFile(archive) as source:
        content = source.read("Online Retail.xlsx")
    if sha256(content).hexdigest() != WORKBOOK_SHA256:
        raise ValueError("L’empreinte du fichier ne correspond pas au classeur UCI documenté.")

    output.write(START_IMPORT)
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        rows = workbook["Online Retail"].values
        headers = next(rows)
        fields = ("InvoiceNo", "InvoiceDate", "Country", "Quantity", "UnitPrice")
        indexes = [headers.index(field) for field in fields]
        writer = csv.writer(output, lineterminator="\n")
        count = 0
        for count, row in enumerate(rows, start=1):
            writer.writerow([row[index] for index in indexes])
        if count != LINE_COUNT:
            raise ValueError(f"{count} lignes lues, {LINE_COUNT} attendues.")
        output.write(FINISH_IMPORT)
    finally:
        workbook.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Archive officielle online+retail.zip")
    default_cli = shutil.which("docker")
    if not default_cli and os.environ.get("LOCALAPPDATA"):
        default_cli = str(
            Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
        )
    parser.add_argument("--docker", default=default_cli or "docker")
    args = parser.parse_args()
    with TemporaryFile(mode="w+b") as raw:
        with TextIOWrapper(raw, encoding="utf-8", newline="") as sql:
            write_import(args.archive, sql)
            sql.flush()
            raw.seek(0)
            subprocess.run(
                [
                    args.docker,
                    "compose",
                    "exec",
                    "-T",
                    "db",
                    "sh",
                    "-c",
                    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                ],
                stdin=raw,
                cwd=Path(__file__).resolve().parents[1],
                check=True,
            )


if __name__ == "__main__":
    main()
