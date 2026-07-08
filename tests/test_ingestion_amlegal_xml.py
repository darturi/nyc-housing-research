from io import BytesIO
from zipfile import ZipFile

import pytest

from app.db.session import SessionLocal
from app.ingestion.amlegal_xml import (
    HMC_XML_MEMBER,
    hmc_xml_from_zip,
    parse_hmc_bulk_xml_document,
    parse_hmc_xml_sections,
)
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
)
from app.ingestion.registry import seed_sources
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.source import Source


def test_hmc_xml_from_zip_extracts_required_member():
    zip_content = zip_with_members({HMC_XML_MEMBER: hmc_xml_fixture()})

    xml_content = hmc_xml_from_zip(zip_content)

    assert b"27-2005" in xml_content


def test_hmc_xml_from_zip_requires_hmc_member():
    zip_content = zip_with_members({"XML/other.xml": b"<DOCUMENT />"})

    with pytest.raises(ValueError, match=HMC_XML_MEMBER):
        hmc_xml_from_zip(zip_content)


def test_parse_hmc_xml_sections_extracts_citations_and_titles():
    sections = parse_hmc_xml_sections(hmc_xml_fixture())

    assert len(sections) == 2
    assert sections[0].citation == "NYC Admin Code § 27-2005"
    assert sections[0].title == "Duties of owner."
    assert "good repair" in sections[0].text
    assert sections[1].citation == "NYC Admin Code § 27-2029"


def test_parse_hmc_bulk_xml_document_persists_citation_chunks(tmp_path):
    from app.core.config import get_settings

    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path)
    zip_content = zip_with_members({HMC_XML_MEMBER: hmc_xml_fixture()})
    artifact = DownloadedArtifact(
        content=zip_content,
        content_hash=hash_bytes(zip_content),
        content_type="application/zip",
        byte_size=len(zip_content),
        extension="zip",
        source_url="https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-60027",
    )
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="nyc-housing-maintenance-code").one()
        source_version = create_or_get_source_version(db, source, artifact)

        parse_hmc_bulk_xml_document(db, source, source_version, zip_content)

        chunks = db.query(Chunk).order_by(Chunk.order_index).all()
        citation = (
            db.query(Citation)
            .filter_by(normalized_citation="NYC Admin Code § 27-2005")
            .one()
        )

    assert len(chunks) == 2
    assert chunks[0].citation == "NYC Admin Code § 27-2005"
    assert citation.chunk_id == chunks[0].id


def zip_with_members(members: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def hmc_xml_fixture() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<DOCUMENT>
  <LEVEL style-name="Chapter">
    <LEVEL style-name="Section">
      <RECORD id="section-27-2005">
        <HEADING>\xc2\xa7 27-2005 Duties of owner.</HEADING>
        <PARA>\xc2\xa7 27-2005 <CHARFORMAT>Duties of owner.</CHARFORMAT></PARA>
      </RECORD>
      <LEVEL style-name="Normal Level">
        <RECORD>
          <PARA>The owner shall keep the premises in good repair.</PARA>
        </RECORD>
      </LEVEL>
    </LEVEL>
    <LEVEL style-name="Section">
      <RECORD id="section-27-2029">
        <HEADING>\xc2\xa7 27-2029 Heat required.</HEADING>
        <PARA>\xc2\xa7 27-2029 <CHARFORMAT>Heat required.</CHARFORMAT></PARA>
      </RECORD>
      <LEVEL style-name="Normal Level">
        <RECORD>
          <PARA>Owners must provide heat during the heat season.</PARA>
        </RECORD>
      </LEVEL>
    </LEVEL>
  </LEVEL>
</DOCUMENT>
"""
