from memory.common import scopes


def test_ingest_scope_registered():
    assert scopes.SCOPE_INGEST == "ingest"
    assert scopes.SCOPE_INGEST in scopes.ALL_SCOPE_VALUES
    assert not scopes.validate_scopes([scopes.SCOPE_INGEST])
    assert any(s["value"] == scopes.SCOPE_INGEST for s in scopes.VALID_SCOPES)
