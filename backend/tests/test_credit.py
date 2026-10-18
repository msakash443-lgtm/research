from app.credit import CREDIT_ROLES, InvalidCreditRole, validate_roles


def test_the_fourteen_standard_credit_roles_are_shipped():
    assert len(CREDIT_ROLES) == 14
    assert "Conceptualization" in CREDIT_ROLES
    assert "Writing - original draft" in CREDIT_ROLES


def test_validate_roles_accepts_known_roles_in_taxonomy_order():
    assert validate_roles(["Supervision", "Conceptualization"]) == ["Conceptualization", "Supervision"]


def test_validate_roles_drops_duplicates():
    assert validate_roles(["Methodology", "Methodology"]) == ["Methodology"]


def test_validate_roles_rejects_an_unknown_role_naming_it():
    try:
        validate_roles(["Conceptualization", "Peer review"])
        assert False, "expected InvalidCreditRole"
    except InvalidCreditRole as exc:
        assert "Peer review" in str(exc)


def test_empty_role_list_is_valid():
    assert validate_roles([]) == []
