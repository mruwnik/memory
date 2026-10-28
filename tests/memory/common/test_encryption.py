from memory.common import crypto, encryption


def test_key_derivation_runs_once_per_secret(monkeypatch):
    derived = []
    real = crypto.PBKDF2HMAC

    def counting(*args, **kwargs):
        derived.append(kwargs["salt"])
        return real(*args, **kwargs)

    monkeypatch.setattr(crypto, "PBKDF2HMAC", counting)
    monkeypatch.setattr(encryption.settings, "SECRETS_ENCRYPTION_KEY", "key-derive-once")
    crypto.derive_encryption_key.cache_clear()

    tokens = [encryption.encrypt_value(f"token-{i}") for i in range(3)]

    assert [encryption.decrypt_value(t) for t in tokens] == ["token-0", "token-1", "token-2"]
    assert len(derived) == 1


def test_a_changed_secret_derives_a_new_key(monkeypatch):
    monkeypatch.setattr(encryption.settings, "SECRETS_ENCRYPTION_KEY", "first")
    first = encryption.get_encryption_key()
    monkeypatch.setattr(encryption.settings, "SECRETS_ENCRYPTION_KEY", "second")

    assert encryption.get_encryption_key() != first
