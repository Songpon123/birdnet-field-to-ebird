"""Human review of BirdNET clips. Only approved clips are copied to Ready/."""

import hashlib
import os
import shutil
from pathlib import Path

from openpyxl import load_workbook


REVIEW_COLUMNS = ("Review status", "Reviewed common", "Reviewed scientific",
                  "Quality rating", "Review notes", "Ready file", "Ready sha256")


def _headers(sheet):
    return {str(cell.value): cell.column for cell in sheet[1] if cell.value is not None}


def _safe_part(value):
    result = "".join(c if c.isalnum() or c in " -_." else "_" for c in str(value)).strip(" .")
    return result or "Unknown"


def _within(parent: Path, relative: str) -> Path:
    path = (parent / relative).resolve()
    if not path.is_relative_to(parent.resolve()):
        raise ValueError("Clip path is outside the output folder")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_reviews(summary: Path):
    book = load_workbook(summary, read_only=True, data_only=True)
    try:
        sheet = book.active
        headers = _headers(sheet)
        if "File" not in headers:
            raise ValueError("summary.xlsx has no File column")
        result = []
        for row in sheet.iter_rows(min_row=2):
            values = {name: row[col - 1].value for name, col in headers.items()}
            if values.get("File"):
                values["_row"] = row[0].row
                result.append(values)
        return result
    finally:
        book.close()


def save_review(summary: Path, row_number: int, *, status: str, common: str,
                scientific: str, rating=None, notes="", expected_file=None):
    if status not in ("Pending", "Approved", "Rejected"):
        raise ValueError("Invalid review status")
    common, scientific = common.strip(), scientific.strip()
    if status == "Approved" and (not common or not scientific):
        raise ValueError("Enter both common and scientific names before approval")
    if status == "Approved" and rating not in (1, 2, 3, 4, 5):
        raise ValueError("Choose a human audio quality rating from 1 to 5")

    book = load_workbook(summary)
    sheet = book.active
    headers = _headers(sheet)
    if "File" not in headers or not 2 <= row_number <= sheet.max_row:
        book.close()
        raise ValueError("Selected clip is no longer in the summary")
    for name in REVIEW_COLUMNS:
        if name not in headers:
            col = sheet.max_column + 1
            sheet.cell(1, col, name)
            headers[name] = col

    def cell(name):
        return sheet.cell(row_number, headers[name])

    relative = str(cell("File").value or "")
    if not relative:
        book.close()
        raise ValueError("Selected row has no audio file")
    if expected_file is not None and relative != expected_file:
        book.close()
        raise ValueError("Summary changed since it was loaded; reload before reviewing")
    date_folder = summary.parent
    original = _within(date_folder, relative)
    if not original.is_file():
        book.close()
        raise FileNotFoundError(original)
    if status == "Approved" and original.stat().st_size > 500_000_000:
        book.close()
        raise ValueError("Clip exceeds the 500 MB eBird upload limit; shorten it before approval")
    previous = str(cell("Ready file").value or "")
    previous_hash = str(cell("Ready sha256").value or "")
    old_ready = None
    if previous:
        old_ready = _within(date_folder, previous)
        if not old_ready.is_relative_to((date_folder / "Ready").resolve()):
            book.close()
            raise ValueError("Saved Ready path is outside Ready folder")
        if old_ready.is_file() and (not previous_hash or _sha256(old_ready) != previous_hash):
            book.close()
            raise FileExistsError(f"Ready copy was changed outside the program: {old_ready}")
    ready_relative = ""
    ready_hash = ""

    if status == "Approved":
        clip_id = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
        out_name = (f"{date_folder.name}_{clip_id}_{_safe_part(scientific).replace(' ', '.')}_"
                    f"R{rating}{original.suffix.lower()}")
        ready = date_folder / "Ready" / _safe_part(common) / out_name
        if ready.is_file() and (previous != str(ready.relative_to(date_folder)) or
                                not previous_hash or _sha256(ready) != previous_hash):
            book.close()
            raise FileExistsError(f"Ready copy was changed outside the program: {ready}")
        ready.parent.mkdir(parents=True, exist_ok=True)
        temporary = ready.with_name(ready.name + ".tmp")
        try:
            shutil.copy2(original, temporary)
            os.replace(temporary, ready)
        finally:
            temporary.unlink(missing_ok=True)
        ready_relative = str(ready.relative_to(date_folder))
        ready_hash = _sha256(ready)

    cell("Review status").value = status
    cell("Reviewed common").value = common
    cell("Reviewed scientific").value = scientific
    cell("Quality rating").value = rating if status == "Approved" else None
    cell("Review notes").value = notes.strip()
    cell("Ready file").value = ready_relative
    cell("Ready sha256").value = ready_hash
    temporary_summary = summary.with_name(summary.stem + ".review.tmp.xlsx")
    try:
        book.save(temporary_summary)
        os.replace(temporary_summary, summary)
    except Exception:
        if ready_relative and ready_relative != previous:
            new_ready = _within(date_folder, ready_relative)
            if new_ready.is_file() and _sha256(new_ready) == ready_hash:
                new_ready.unlink()
        raise
    finally:
        temporary_summary.unlink(missing_ok=True)
        book.close()

    if previous and previous != ready_relative:
        if old_ready.is_file():
            old_ready.unlink()
    return ready_relative
