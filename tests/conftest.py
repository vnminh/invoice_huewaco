from pathlib import Path
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

# Unit tests use deterministic hash vectors; the real semantic model has a separate benchmark.
os.environ['EMBEDDING_MODEL'] = 'hash'

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base, make_engine, sessions


@pytest.fixture
def db(tmp_path):
    engine = make_engine('sqlite:///' + str(tmp_path / 'test.sqlite'))
    Base.metadata.create_all(engine)
    yield engine, sessions(engine)
    engine.dispose()


def workbook(path, rows):
    """Minimal XLSX fixture with shared strings, using no full workbook library."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    strings = []
    sheet = ET.Element('worksheet', xmlns=ns)
    data = ET.SubElement(sheet, 'sheetData')
    for index, values in rows:
        row = ET.SubElement(data, 'row', r=str(index))
        for col, value in values.items():
            cell = ET.SubElement(row, 'c', r=col+str(index))
            if isinstance(value, str):
                cell.set('t', 's')
                strings.append(value)
                value = len(strings)-1
            ET.SubElement(cell, 'v').text = str(value)
    shared = ET.Element('sst', xmlns=ns)
    for value in strings:
        ET.SubElement(ET.SubElement(shared, 'si'), 't').text = value
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/sharedStrings.xml', ET.tostring(shared))
        z.writestr('xl/worksheets/sheet1.xml', ET.tostring(sheet))
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="BIDV" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
    return path
