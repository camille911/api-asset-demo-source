"""Unit tests for URL validation and secret redaction."""
import pytest

from git_asset_mcp.security import redact_secrets, validate_repository_url


class TestValidateRepositoryUrl:
    def test_valid_github_url(self):
        assert (
            validate_repository_url("https://github.com/camille911/B_projet.git")
            == "https://github.com/camille911/B_projet.git"
        )

    def test_strips_trailing_slash(self):
        assert validate_repository_url("https://github.com/owner/repo/") == "https://github.com/owner/repo"

    def test_reject_file_scheme(self):
        with pytest.raises(ValueError):
            validate_repository_url("file:///etc/passwd")

    def test_reject_ssh_scheme(self):
        with pytest.raises(ValueError):
            validate_repository_url("ssh://git@github.com/foo/bar.git")

    def test_reject_wrong_host(self):
        with pytest.raises(ValueError):
            validate_repository_url("https://evil.example.com/foo/bar.git")

    def test_reject_embedded_credentials(self):
        with pytest.raises(ValueError):
            validate_repository_url("https://user:token@github.com/foo/bar.git")

    def test_reject_empty(self):
        with pytest.raises(ValueError):
            validate_repository_url("")


class TestRedactSecrets:
    def test_redact_github_pat(self):
        out = redact_secrets("token is ghp_abcdefghijklmnopqrstuvwxyz1234")
        assert "ghp_abcdefghijklmnopqrstuvwxyz1234" not in out
        assert "REDACTED" in out

    def test_redact_url_credentials(self):
        out = redact_secrets("https://user:secret@github.com/foo.git")
        assert "secret" not in out

    def test_redact_bearer(self):
        out = redact_secrets("Authorization: Bearer abc123def456")
        assert "abc123def456" not in out

    def test_passthrough_clean_text(self):
        assert redact_secrets("just a normal message") == "just a normal message"
