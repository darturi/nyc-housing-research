import time
import zipfile
from datetime import UTC, datetime
from io import BytesIO

import httpx
from fastapi.testclient import TestClient

from app.cli.main import build_parser
from app.corpus.download import download_source_artifacts
from app.corpus.manifests import (
    load_all_manifests,
    load_core_manifests,
    load_source_packs,
)
from app.corpus.packs import SourcePackService
from app.corpus.service import CorpusService, SourceArtifact
from app.ingestion.amlegal_xml import parse_nyc_admin_xml_range_sections
from app.ingestion.hpd_guidance import pages_from_bundle
from app.local_app import create_local_app
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext


def _rent_law_zip(section_501: str = "Declaration of emergency") -> bytes:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <ROOT>
      <LEVEL style-name="Section"><RECORD>
        <HEADING>§ 11-101 Unrelated title</HEADING>
        <PARA>This unrelated Administrative Code title must be ignored safely.</PARA>
      </RECORD></LEVEL>
      <LEVEL style-name="Section"><RECORD>
        <HEADING>§ 26-501 {section_501}</HEADING>
        <PARA>The council finds a serious public emergency in housing.</PARA>
      </RECORD></LEVEL>
      <LEVEL style-name="Section"><RECORD>
        <HEADING>§ 26-510 Adjustment of rents</HEADING>
        <PARA>This is a middle section in the configured source range.</PARA>
      </RECORD></LEVEL>
      <LEVEL style-name="Section"><RECORD>
        <HEADING>§ 26-520 Short title</HEADING>
        <PARA>This chapter may be cited by its short title.</PARA>
      </RECORD></LEVEL>
      <LEVEL style-name="Section"><RECORD>
        <HEADING>§ 27-2005 Duties of owner</HEADING>
        <PARA>This section must not be included in the rent-law module.</PARA>
      </RECORD></LEVEL>
    </ROOT>""".encode()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("XML/rent-law.xml", xml)
    return stream.getvalue()


def _artifact(section_501: str = "Declaration of emergency") -> SourceArtifact:
    return SourceArtifact(
        slug="nyc-rent-stabilization-law",
        content=_rent_law_zip(section_501),
        source_url="https://files.amlegal.com/pdffiles/NewYorkCity/Admin/XML.zip",
        content_type="application/zip",
        retrieved_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def test_pack_registry_keeps_optional_modules_out_of_core() -> None:
    pack = load_source_packs()["rent-regulation"]
    core = load_core_manifests()
    all_manifests = load_all_manifests()

    assert pack.support_status == "partial"
    assert set(pack.module_slugs).isdisjoint(core)
    assert all(all_manifests[slug].role == "optional" for slug in pack.module_slugs)
    assert all(all_manifests[slug].pack_id == pack.id for slug in pack.module_slugs)
    assert pack.missing_modules


def test_rent_stabilization_parser_extracts_only_configured_range() -> None:
    sections = parse_nyc_admin_xml_range_sections(
        _rent_law_zip(),
        section_start="26-501",
        section_end="26-520",
    )

    assert [section.citation for section in sections] == [
        "NYC Admin Code § 26-501",
        "NYC Admin Code § 26-510",
        "NYC Admin Code § 26-520",
    ]
    assert "27-2005" not in " ".join(section.text for section in sections)


def test_optional_pack_install_remove_and_restore_preserves_core_readiness(
    tmp_path,
) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    try:
        corpus = CorpusService(storage)
        installed_generation = corpus.install_artifacts(
            [_artifact()], allow_partial=True
        )
        status = corpus.status()
        assert status.active_generation_id == installed_generation
        assert status.readiness == "partial_text_ready"
        assert status.is_partial is True

        pack = SourcePackService(corpus).get("rent-regulation")
        assert pack["status"] == "partially_installed"
        assert pack["installed_module_count"] == 1
        preview = SourcePackService(corpus).removal_preview(
            "rent-regulation", ["nyc-rent-stabilization-law"]
        )
        assert preview["installed_modules_to_remove"] == ["nyc-rent-stabilization-law"]

        removed_generation = corpus.remove_sources(["nyc-rent-stabilization-law"])
        assert removed_generation != installed_generation
        assert corpus.status().source_count == 0

        restored_generation = corpus.restore_sources(["nyc-rent-stabilization-law"])
        assert restored_generation != removed_generation
        assert corpus.status().source_count == 1
    finally:
        storage.close()


def test_curated_guidance_download_uses_manifest_allowlist(tmp_path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        title = request.url.path.strip("/").replace("-", " ").title()
        return httpx.Response(
            200,
            text=(
                f"<html><title>{title}</title><main><h1>{title}</h1>"
                "<p>Official agency guidance with enough useful text to create "
                "a validated searchable section for local research.</p></main></html>"
            ),
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    context = WorkspaceContext.from_options(tmp_path / "workspace", environment={})
    artifact = download_source_artifacts(
        context,
        ["dhcr-rent-regulation-guidance"],
        transport=httpx.MockTransport(handler),
    )[0]

    assert artifact.content_type == "application/json"
    assert len(pages_from_bundle(artifact.content)) == 5
    assert len(seen) == 5
    assert all(url.startswith("https://hcr.ny.gov/") for url in seen)


def test_pack_check_validates_without_activating_an_update(tmp_path) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    try:
        corpus = CorpusService(storage)
        active = corpus.install_artifacts([_artifact()], allow_partial=True)
        result = corpus.check_artifacts([_artifact("Revised emergency finding")])

        assert result[0]["state"] == "update_available"
        assert result[0]["active_content_hash"] != result[0]["checked_content_hash"]
        assert corpus.status().active_generation_id == active
    finally:
        storage.close()


def test_pack_api_supports_install_preview_remove_and_restore(
    tmp_path, monkeypatch
) -> None:
    def downloader(_context, slugs, *, progress):
        assert slugs == ["nyc-rent-stabilization-law"]
        progress("Downloading source 1/1: nyc-rent-stabilization-law")
        return [_artifact()]

    monkeypatch.setattr("app.jobs.maintenance.download_source_artifacts", downloader)
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    storage.close()
    app = create_local_app(context)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session = client.post(
            "/api/v1/session/exchange",
            headers={"Origin": "http://127.0.0.1"},
            json={"launch_token": app.state.launch_token},
        )
        csrf = session.json()["csrf_token"]
        headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": csrf,
        }

        listing = client.get("/api/v1/source-packs")
        assert listing.status_code == 200
        assert listing.json()["source_packs"][0]["support_status"] == "partial"

        created = client.post(
            "/api/v1/source-packs/rent-regulation/jobs",
            headers=headers,
            json={
                "operation": "install",
                "modules": ["nyc-rent-stabilization-law"],
            },
        )
        assert created.status_code == 202
        assert _wait_job(client, created.json()["id"])["state"] == "succeeded"

        preview = client.post(
            "/api/v1/source-packs/rent-regulation/jobs",
            headers=headers,
            json={
                "operation": "remove",
                "modules": ["nyc-rent-stabilization-law"],
            },
        )
        assert preview.status_code == 200
        assert preview.json()["installed_modules_to_remove"] == [
            "nyc-rent-stabilization-law"
        ]

        removed = client.post(
            "/api/v1/source-packs/rent-regulation/jobs",
            headers=headers,
            json={
                "operation": "remove",
                "modules": ["nyc-rent-stabilization-law"],
                "apply": True,
            },
        )
        assert _wait_job(client, removed.json()["id"])["state"] == "succeeded"
        removed_detail = client.get("/api/v1/source-packs/rent-regulation").json()
        assert removed_detail["installed_module_count"] == 0
        assert removed_detail["restorable_module_count"] == 1

        restored = client.post(
            "/api/v1/source-packs/rent-regulation/jobs",
            headers=headers,
            json={
                "operation": "restore",
                "modules": ["nyc-rent-stabilization-law"],
            },
        )
        assert _wait_job(client, restored.json()["id"])["state"] == "succeeded"
        detail = client.get("/api/v1/source-packs/rent-regulation").json()
        assert detail["installed_module_count"] == 1
        checked = client.post(
            "/api/v1/source-packs/rent-regulation/jobs",
            headers=headers,
            json={
                "operation": "check",
                "modules": ["nyc-rent-stabilization-law"],
            },
        )
        checked_job = _wait_job(client, checked.json()["id"])
        assert checked_job["state"] == "succeeded"
        assert checked_job["resume"]["check_result"][0]["state"] == "current"
        assert "generation_id" not in checked_job["resume"]
        search = client.get(
            "/api/v1/search",
            params={"q": "NYC Admin Code 26-501 rent stabilization"},
        ).json()
        notice = search["coverage"]["source_pack_notices"][0]
        assert notice["coverage_status"] == "partial_topic_coverage"
        assert notice["unavailable_modules"][0]["slug"] == (
            "dhcr-rent-regulation-guidance"
        )
        assert search["results"][0]["category"] == "statute"


def test_pack_cli_contract_requires_explicit_remove_apply() -> None:
    parser = build_parser()
    preview = parser.parse_args(["packs", "remove", "rent-regulation"])
    applied = parser.parse_args(["packs", "remove", "rent-regulation", "--apply"])

    assert preview.apply is False
    assert applied.apply is True


def _wait_job(client: TestClient, job_id: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for _ in range(200):
        result = client.get(f"/api/v1/jobs/{job_id}").json()
        if result["state"] in {"succeeded", "failed", "cancelled"}:
            return result
        time.sleep(0.01)
    raise AssertionError(f"Job did not finish: {result}")
