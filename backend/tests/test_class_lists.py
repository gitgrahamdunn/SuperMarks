from __future__ import annotations

import zipfile
from io import BytesIO

from app.class_lists import extract_names_from_rows, parse_class_list_csv_bytes, parse_class_list_xlsx_bytes


def _tiny_xlsx_bytes() -> bytes:
    workbook = BytesIO()
    with zipfile.ZipFile(workbook, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
              <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
              <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
            </Types>""",
        )
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
            </workbook>""",
        )
        zf.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="5" uniqueCount="5">
              <si><t>First Name</t></si>
              <si><t>Last Name</t></si>
              <si><t>Jordan</t></si>
              <si><t>Lee</t></si>
              <si><t>Avery Stone</t></si>
            </sst>""",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
                <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>
                <row r="3"><c r="A3" t="s"><v>4</v></c></row>
              </sheetData>
            </worksheet>""",
        )
    return workbook.getvalue()


def test_extract_names_from_rows_combines_first_and_last_columns() -> None:
    names = extract_names_from_rows([
        ["First Name", "Last Name"],
        ["Jordan", "Lee"],
        ["Avery Stone"],
    ])

    assert names == ["Jordan Lee", "Avery Stone"]


def test_extract_names_from_rows_combines_last_and_first_columns() -> None:
    names = extract_names_from_rows([
        ["Last Name", "First Name"],
        ["Lee", "Jordan"],
        ["Stone", "Avery"],
    ])

    assert names == ["Jordan Lee", "Avery Stone"]


def test_extract_names_from_rows_reorders_single_cell_last_first_names() -> None:
    names = extract_names_from_rows([
        ["Last Name", "First Name"],
        ["Lee Jordan"],
        ["Stone Avery"],
    ])

    assert names == ["Jordan Lee", "Avery Stone"]


def test_extract_names_from_rows_strips_commas_from_last_first_names() -> None:
    names = extract_names_from_rows([
        ["Student Name"],
        ["Lee, Jordan"],
        ["Stone, Avery"],
    ])

    assert names == ["Jordan Lee", "Avery Stone"]


def test_parse_class_list_csv_bytes_reads_student_names() -> None:
    payload = b"Student Name,ID\nJordan Lee,1001\nAvery Stone,1002\n"

    names = parse_class_list_csv_bytes(payload)

    assert names == ["Jordan Lee", "Avery Stone"]


def test_parse_class_list_xlsx_bytes_reads_student_names() -> None:
    names = parse_class_list_xlsx_bytes(_tiny_xlsx_bytes())

    assert names == ["Jordan Lee", "Avery Stone"]
