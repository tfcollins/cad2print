import pytest

from cad2print.urlparse import OnshapeRef, parse_onshape_url

DID = "80ea6bbfc116a259b466d2b6"
WID = "1bb98e0d403beac7aada3478"
EID = "b25c134b56b75f72715903bd"


def test_workspace_url_with_element():
    ref = parse_onshape_url(f"https://cad.onshape.com/documents/{DID}/w/{WID}/e/{EID}")
    assert ref == OnshapeRef(did=DID, wvm="w", wvmid=WID, eid=EID)


def test_version_url():
    ref = parse_onshape_url(f"https://cad.onshape.com/documents/{DID}/v/{WID}/e/{EID}")
    assert ref.wvm == "v"


def test_microversion_url():
    ref = parse_onshape_url(f"https://cad.onshape.com/documents/{DID}/m/{WID}/e/{EID}")
    assert ref.wvm == "m"


def test_query_string_and_fragment_are_ignored():
    ref = parse_onshape_url(
        f"https://cad.onshape.com/documents/{DID}/w/{WID}/e/{EID}?renderMode=0&uiState=abc#x"
    )
    assert ref.eid == EID


def test_url_without_element_has_no_eid():
    ref = parse_onshape_url(f"https://cad.onshape.com/documents/{DID}/w/{WID}")
    assert ref == OnshapeRef(did=DID, wvm="w", wvmid=WID, eid=None)


def test_surrounding_whitespace_is_tolerated():
    ref = parse_onshape_url(f"  https://cad.onshape.com/documents/{DID}/w/{WID}/e/{EID}\n")
    assert ref.did == DID


def test_enterprise_subdomain_is_accepted():
    ref = parse_onshape_url(f"https://acme.onshape.com/documents/{DID}/w/{WID}/e/{EID}")
    assert ref.did == DID


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not a url",
        "https://example.com/documents/abc/w/def/e/ghi",
        f"https://cad.onshape.com/documents/{DID}",
        f"https://cad.onshape.com/documents/{DID}/x/{WID}/e/{EID}",
        f"https://cad.onshape.com/documents/{DID}/w/{WID}/e/",
    ],
)
def test_garbage_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_onshape_url(bad)
