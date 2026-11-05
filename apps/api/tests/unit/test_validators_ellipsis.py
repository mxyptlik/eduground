"""Regression: ellipsis names/keys are not traversal; the error handler serializes."""

from __future__ import annotations

import pytest

from app.schemas.validators import validate_filename, validate_storage_key


def test_ellipsis_names_and_storage_keys_pass_validation():
    name = "Computer Security an... by Paul C. van Oorschot (z-lib.org).pdf"
    assert validate_filename(name) == name
    key = ("notebooks/n1/uploads/aa/"
           "bb_Computer_Security_an..._by_Paul_C._van_Oorschot_z-lib.org.pdf")
    assert validate_storage_key(key) == key


def test_real_traversal_is_still_rejected():
    with pytest.raises(ValueError):
        validate_storage_key("notebooks/../secrets")
    with pytest.raises(ValueError):
        validate_filename("../evil.pdf")
