from dataclasses import replace
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from app.corpus.download import SourceDownloadError, _get
from app.corpus.resource_parsers import parse_resource
from app.hpd.connector import (
    BuildingCandidate,
    HpdSocrataConnector,
    PropertyQuery,
    _record,
)
from app.research.dossiers import DossierError, PropertyDossierService
from app.workspace.network import NetworkPolicy
from tests.test_hardening_safety import empty_property
from tests.test_hardening_safety import workspace as workspace


def docx(body):
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<Types>application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml</Types>",
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + body
            + "</w:body></w:document>",
        )
    return content.getvalue()


def test_docx_preserves_controlled_clauses_and_bounds_long_multilingual_blocks():
    body = "<w:p><w:r><w:t>Ordinary text.</w:t></w:r></w:p>"
    body += (
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>Important controlled clause."
        "</w:t></w:r></w:p></w:sdtContent></w:sdt>"
    )
    body += "<w:ins><w:p><w:r><w:t>Inserted block.</w:t></w:r></w:p></w:ins>"
    body += "<w:del><w:p><w:r><w:t>Deleted block.</w:t></w:r></w:p></w:del>"
    for wrapper in ("p", "tbl"):
        paragraph = (
            "<w:p><w:r><w:t>"
            + ("界🙂" * 5000)
            + "Late decisive clause.</w:t></w:r></w:p>"
        )
        body += (
            paragraph
            if wrapper == "p"
            else "<w:tbl><w:tr><w:tc>" + paragraph + "</w:tc></w:tr></w:tbl>"
        )
    parsed = parse_resource(
        docx(body), filename="clauses.docx", media_type=None, title="Clauses"
    )
    extracted = " ".join(chunk.text for chunk in parsed.chunks)
    assert "Important controlled clause." in extracted
    assert "Inserted block." in extracted
    assert "Deleted block." not in extracted
    assert extracted.count("Late decisive clause.") == 2
    assert all(len(chunk.text) <= 4000 for chunk in parsed.chunks)
    assert all(len(chunk.text.encode()) <= 7500 for chunk in parsed.chunks)
    assert len({chunk.stable_id for chunk in parsed.chunks}) == len(parsed.chunks)
    assert any(
        chunk.locator.get("character_start", 0) > 4000 for chunk in parsed.chunks
    )


def test_unknown_docx_text_container_emits_warning():
    body = (
        "<w:p><w:r><w:t>Usable text.</w:t></w:r></w:p>"
        "<w:unknown><w:r><w:t>Unsupported clause.</w:t></w:r></w:unknown>"
    )
    parsed = parse_resource(
        docx(body), filename="unknown.docx", media_type=None, title="Unknown"
    )
    assert any("omitted text" in warning for warning in parsed.warnings)


@pytest.mark.parametrize("case", ["wrong_zero", "mixed", "unresolved"])
def test_dossiers_reject_unbound_or_mixed_building_evidence(workspace, case):
    _context, storage = workspace
    service = PropertyDossierService(storage)
    candidate = BuildingCandidate("111", None, "MANHATTAN", "1", "MAIN STREET", "10001")
    valid = replace(empty_property(), candidates=(candidate,))
    identity = service.resolve(valid)
    assert (
        service.create_observation(identity["id"], valid)["payload"]["panels"][
            "hpd_violations"
        ]["status"]
        == "complete"
    )
    if case == "wrong_zero":
        invalid = empty_property("222")
    elif case == "unresolved":
        invalid = replace(valid, requires_selection=True)
    else:
        invalid = replace(
            valid,
            records=(
                _record({"violationid": "1", "buildingid": "111"}),
                _record({"violationid": "2", "buildingid": "222"}),
            ),
        )
    with pytest.raises(DossierError, match="confirmed building"):
        service.create_observation(identity["id"], invalid)


def test_hpd_pagination_includes_undated_partition_without_duplicates():
    dated = {
        "violationid": "1",
        "buildingid": "111",
        "inspectiondate": "2026-09-01T00:00:00.000",
    }
    undated = [{"violationid": str(i), "buildingid": "111"} for i in (2, 3)]
    queries = []

    def handler(request):
        where = request.url.params["$where"]
        queries.append(where)
        if "inspectiondate is null" in where:
            rows = undated
            if "violationid > '2'" in where:
                rows = [undated[-1]]
        else:
            rows = [] if "inspectiondate <" in where else [dated]
        return httpx.Response(200, json=rows[: int(request.url.params["$limit"])])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = HpdSocrataConnector(NetworkPolicy(offline=False), client=client)
        query = PropertyQuery(building_id="111", limit=1)
        ids = []
        for _ in range(3):
            result = connector.search(query)
            ids.extend(record.violation_id for record in result.records)
            if result.is_complete:
                break
            assert result.continuation
            query = replace(query, continuation=result.continuation)
        assert result.is_complete
        assert ids == ["1", "2", "3"]
        assert any("inspectiondate is null" in where for where in queries)


def test_undated_only_property_is_not_reported_as_verified_zero():
    def handler(request):
        records = (
            [{"violationid": "2", "buildingid": "111"}]
            if "inspectiondate is null" in request.url.params["$where"]
            else []
        )
        return httpx.Response(200, json=records)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = HpdSocrataConnector(
            NetworkPolicy(offline=False), client=client
        ).search(PropertyQuery(building_id="111"))
    assert result.source_status == "success"
    assert result.is_complete and result.returned_count == 1


def test_download_stops_consuming_an_oversized_stream(workspace, monkeypatch):
    context, _storage = workspace
    monkeypatch.setattr("app.corpus.download.MAX_ARTIFACT_BYTES", 65536)
    consumed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(20):
                consumed.append(index)
                yield b"x" * 32768

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Stream())
        )
    ) as client:
        with pytest.raises(SourceDownloadError):
            _get(context, client, "https://example.com/source", purpose="fixture")
    assert len(consumed) <= 4


def test_base_package_declares_timezone_data():
    import tomllib

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert any(
        dependency.startswith("tzdata")
        for dependency in project["project"]["dependencies"]
    )
