from memory.common import settings


def test_misc_storage_dir_under_file_storage():
    assert settings.MISC_STORAGE_DIR.resolve().is_relative_to(
        settings.FILE_STORAGE_DIR.resolve()
    )
    assert settings.MISC_STORAGE_DIR.exists()


def test_ingest_caps_are_sane():
    assert settings.MAX_MISC_UPLOAD_BYTES >= 1 * 1024 * 1024
    assert 0 < settings.INGEST_INLINE_MAX_BYTES <= settings.MAX_MISC_UPLOAD_BYTES
    assert settings.INGEST_TOKEN_TTL_SECONDS >= 30
