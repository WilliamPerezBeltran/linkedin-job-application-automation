import pytest

from app.domain.exceptions.invalid_email_address_error import InvalidEmailAddressError
from app.domain.value_objects.email_address import EmailAddress

pytestmark = pytest.mark.unit


class TestEmailAddress:
    def test_accepts_a_valid_email(self) -> None:
        email = EmailAddress("someone@example.com")

        assert str(email) == "someone@example.com"

    def test_normalizes_case_and_surrounding_whitespace(self) -> None:
        email = EmailAddress("  Someone@Example.COM  ")

        assert email.value == "someone@example.com"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "not-an-email",
            "missing-domain@",
            "@missing-local.com",
            "no-tld@example",
            "spaces in@example.com",
        ],
    )
    def test_rejects_malformed_values(self, raw: str) -> None:
        with pytest.raises(InvalidEmailAddressError):
            EmailAddress(raw)

    def test_equality_is_by_value(self) -> None:
        assert EmailAddress("a@example.com") == EmailAddress("A@Example.com")
