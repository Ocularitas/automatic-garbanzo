"""Unit tests for `shared.urls.build_document_url` and `SourceLocator`."""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.urls import SourceLocator, build_document_url


@pytest.fixture
def watch_root(tmp_path: Path, monkeypatch) -> Path:
    """Stand up a fake watch folder, point settings at it, and return its path."""
    monkeypatch.setenv("WATCH_FOLDER", str(tmp_path))
    monkeypatch.setenv(
        "PUBLIC_BASE_URL",
        "https://cipoc-abc.uksouth.cloudapp.azure.com/SECRETTOKEN",
    )
    (tmp_path / "contracts" / "saas").mkdir(parents=True)
    (tmp_path / "contracts" / "saas" / "01_brightwave_saas_erp.pdf").touch()
    return tmp_path


def test_url_with_page(watch_root: Path) -> None:
    pdf = watch_root / "contracts" / "saas" / "01_brightwave_saas_erp.pdf"
    url = build_document_url(str(pdf), page=4)
    assert url == (
        "https://cipoc-abc.uksouth.cloudapp.azure.com/SECRETTOKEN/"
        "docs/contracts/saas/01_brightwave_saas_erp.pdf#page=4"
    )


def test_url_without_page(watch_root: Path) -> None:
    pdf = watch_root / "contracts" / "saas" / "01_brightwave_saas_erp.pdf"
    url = build_document_url(str(pdf))
    assert url is not None
    assert url.endswith("/docs/contracts/saas/01_brightwave_saas_erp.pdf")
    assert "#page=" not in url


def test_url_returns_none_when_public_base_url_unset(watch_root: Path,
                                                      monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    pdf = watch_root / "contracts" / "saas" / "01_brightwave_saas_erp.pdf"
    assert build_document_url(str(pdf), page=4) is None


def test_url_returns_none_for_path_outside_watch_folder(
    watch_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path.parent / "elsewhere.pdf"
    assert build_document_url(str(outside), page=4) is None


def test_url_handles_filenames_with_spaces(watch_root: Path) -> None:
    weird = watch_root / "contracts" / "saas" / "with space.pdf"
    weird.touch()
    url = build_document_url(str(weird), page=2)
    assert url is not None
    assert "with%20space.pdf" in url
    assert url.endswith("#page=2")


def test_url_returns_none_for_none_or_empty(watch_root: Path) -> None:
    assert build_document_url(None) is None
    assert build_document_url("") is None


def test_url_treats_zero_or_negative_page_as_no_anchor(watch_root: Path) -> None:
    pdf = watch_root / "contracts" / "saas" / "01_brightwave_saas_erp.pdf"
    assert "#page=" not in (build_document_url(str(pdf), page=0) or "")
    assert "#page=" not in (build_document_url(str(pdf), page=-3) or "")


# --- SourceLocator paths -----------------------------------------------------

def test_locator_with_sharepoint_url_wins_over_file_path(watch_root: Path) -> None:
    """SharePoint is the production source-of-record; if both are set, SP wins.

    Watch-folder serving is the POC fallback only — once SharePoint connector
    is live the watch folder may not even exist for a given document."""
    pdf = watch_root / "contracts" / "saas" / "01_brightwave_saas_erp.pdf"
    locator = SourceLocator(
        file_path=str(pdf),
        sharepoint_url="https://abp.sharepoint.com/sites/contracts/Brightwave.pdf",
    )
    url = build_document_url(locator, page=4)
    assert url == "https://abp.sharepoint.com/sites/contracts/Brightwave.pdf#page=4"


def test_locator_sharepoint_only(watch_root: Path) -> None:
    """No file_path needed when the document lives in SharePoint."""
    locator = SourceLocator(
        sharepoint_url="https://abp.sharepoint.com/sites/contracts/Helio.pdf"
    )
    url = build_document_url(locator, page=2)
    assert url == "https://abp.sharepoint.com/sites/contracts/Helio.pdf#page=2"


def test_locator_file_path_only_falls_back_to_caddy_form(watch_root: Path) -> None:
    pdf = watch_root / "contracts" / "saas" / "01_brightwave_saas_erp.pdf"
    locator = SourceLocator(file_path=str(pdf))
    url = build_document_url(locator, page=4)
    assert url is not None
    assert "/docs/contracts/saas/01_brightwave_saas_erp.pdf" in url
    assert url.endswith("#page=4")


def test_bare_string_still_works_for_back_compat(watch_root: Path) -> None:
    """Existing call sites pass row['file_path'] directly. Must keep working."""
    pdf = watch_root / "contracts" / "saas" / "01_brightwave_saas_erp.pdf"
    url = build_document_url(str(pdf), page=4)
    assert url is not None
    assert "/docs/contracts/saas/01_brightwave_saas_erp.pdf" in url


def test_empty_locator_returns_none() -> None:
    assert build_document_url(SourceLocator()) is None


def test_locator_coerce_handles_three_input_shapes() -> None:
    assert SourceLocator.coerce(None) is None
    assert SourceLocator.coerce("/some/path").file_path == "/some/path"
    sl = SourceLocator(sharepoint_url="https://x")
    assert SourceLocator.coerce(sl) is sl  # same object, not re-wrapped
