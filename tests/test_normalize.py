from st_msads_oci.normalize import normalize_email, normalize_phone, hash_email, hash_phone


def test_email_dots_alias_case_trim():
    assert normalize_email("  Mar.isol.Ortega+promo@Yahoo.com ") == "marisolortega@yahoo.com"


def test_email_accents():
    assert normalize_email("José.Peña@Example.com") == "josepena@example.com"


def test_email_invalid():
    assert normalize_email("") is None
    assert normalize_email(None) is None
    assert normalize_email("no-at-sign") is None
    assert normalize_email(".+x@y.com") is None  # empty local after stripping
    assert normalize_email("a@b@c.com") is None  # multi-@ junk: local still has "@" after rsplit


def test_phone_variants_all_e164():
    for raw in ["(555) 555-1234", "555-555-1234", "5555551234", "1 555 555 1234", "+1 (555) 555-1234"]:
        assert normalize_phone(raw) == "+15555551234"


def test_phone_invalid():
    assert normalize_phone("555-1234") is None
    assert normalize_phone(None) is None


def test_hash_vectors():
    # SHA-256 of the normalized forms, precomputed
    assert hash_email("  Mar.isol.Ortega+promo@Yahoo.com ") == "0243a25fe765b6915e70c6a158bab5a3f5b852dd44ed3d784b14a3dbd3e26032"
    assert hash_email("José.Peña@Example.com") == "34655a04a674650d7f6596ac3df1d2f3025ed9a099c5232a8c5726a79d1e8a97"
    assert hash_phone("(555) 555-1234") == "de6067d59c3a3654e3a3694427e494a4d33eafcbaa940c7e3921cf921972b014"
    assert hash_email("junk") is None


import json
from pathlib import Path

def test_shared_vectors():
    vectors = json.loads((Path(__file__).parent / "fixtures" / "normalize_vectors.json").read_text())
    for v in vectors:
        fn = normalize_email if v["kind"] == "email" else normalize_phone
        assert fn(v["raw"]) == v["normalized"], f"vector failed: {v}"
