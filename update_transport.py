"""Update transport UDFs on Sales Orders (ORDR) from Excel file."""

import sys
import openpyxl
from sap_client import SAPClient

INPUT_FILE = "data/input/transport_list.xlsx"


def load_transport_data(path: str) -> list[dict]:
    """Load transport data from Excel. Returns list of dicts with mapped fields."""
    wb = openpyxl.load_workbook(path)
    ws = wb["Transport"]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        doc_num, truck, trailer, load_date, unload_date = row[:5]
        if not isinstance(doc_num, int):
            continue  # skip empty/comment rows
        entry = {
            "DocNum": doc_num,
            "U_TRTractor": truck.strip() if truck else None,
            "U_TRTrailer": trailer.strip() if trailer else None,
            "U_TRLoDatePl1": load_date.strftime("%Y-%m-%d") if load_date else None,
            "U_TRUnloDatePl1": unload_date.strftime("%Y-%m-%d") if unload_date else None,
        }
        rows.append(entry)
    return rows


def resolve_doc_entries(sap: SAPClient, doc_nums: list[int]) -> dict[int, int]:
    """Map DocNum -> DocEntry via SQL (faster than SL for 159 records)."""
    import subprocess

    nums_str = ",".join(str(n) for n in doc_nums)
    query = f"SET NOCOUNT ON; SELECT DocNum, DocEntry FROM ORDR WHERE DocNum IN ({nums_str})"
    result = subprocess.run(
        ["ssh", "sap-prod", f'sqlcmd -S localhost -d SBO_DOMSON_PL -U sap_sync_reader -P SapSyncR3ad2026 -Q "{query}" -W -s"|" -h-1'],
        capture_output=True, text=True, timeout=30,
    )
    mapping = {}
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("-"):
            continue
        parts = line.split("|")
        if len(parts) == 2:
            try:
                mapping[int(parts[0].strip())] = int(parts[1].strip())
            except ValueError:
                continue
    return mapping


def main(dry_run: bool = True):
    print(f"Loading data from {INPUT_FILE}...")
    rows = load_transport_data(INPUT_FILE)
    print(f"Loaded {len(rows)} transport records")

    doc_nums = [r["DocNum"] for r in rows]
    print(f"Resolving DocEntry for {len(doc_nums)} orders via SQL...")
    doc_map = resolve_doc_entries(None, doc_nums)
    print(f"Found {len(doc_map)} matching orders in SAP")

    missing = [n for n in doc_nums if n not in doc_map]
    if missing:
        print(f"WARNING: {len(missing)} DocNums not found in SAP: {missing[:10]}...")

    if dry_run:
        print(f"\n=== DRY RUN — showing first 5 updates ===")
        for row in rows[:5]:
            doc_num = row["DocNum"]
            doc_entry = doc_map.get(doc_num)
            payload = {k: v for k, v in row.items() if k != "DocNum" and v is not None}
            print(f"  PATCH /Orders({doc_entry})  [DocNum={doc_num}]")
            print(f"    {payload}")
        print(f"\n  ... and {len(rows) - 5} more")
        print(f"\nTo execute, run: python update_transport.py --execute")
        return

    # Execute updates
    print(f"\nExecuting {len(doc_map)} updates via Service Layer...")
    with SAPClient() as sap:
        ok, fail = 0, 0
        for i, row in enumerate(rows):
            doc_num = row["DocNum"]
            doc_entry = doc_map.get(doc_num)
            if not doc_entry:
                continue
            payload = {k: v for k, v in row.items() if k != "DocNum" and v is not None}
            try:
                sap.patch(f"/Orders({doc_entry})", payload)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  FAIL DocNum={doc_num} DocEntry={doc_entry}: {e}")
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i + 1}/{len(rows)}")

    print(f"\nDone: {ok} updated, {fail} failed")


if __name__ == "__main__":
    execute = "--execute" in sys.argv
    main(dry_run=not execute)
