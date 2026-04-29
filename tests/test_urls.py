"""Unit tests for `shared.urls.build_document_url`."""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.urls import build_document_url


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
