"""Phone number normalisation to E.164 (users.phone is the login identifier).

Nigerian numbers are accepted in the forms people actually type them:

    08031234567, 0803 123 4567, 8031234567, 2348031234567, +234 803 123 4567

and all become +2348031234567. Numbers from other countries (family abroad)
must be written with their + country code, e.g. +447700900123.
"""

from __future__ import annotations

import re

NIGERIA_CODE = "234"
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
# Nigerian mobile numbers: 10 national digits starting 7, 8 or 9 (e.g. 803..., 905..., 701...).
_NG_NATIONAL = re.compile(r"^[789][01]\d{8}$")


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Return the number in E.164 form or raise InvalidPhoneNumber."""
    if not isinstance(raw, str):
        raise InvalidPhoneNumber("Phone number must be a string")
    value = re.sub(r"[\s\-().]", "", raw.strip())
    if value.startswith("00"):
        value = "+" + value[2:]

    if value.startswith("+"):
        if value.startswith("+" + NIGERIA_CODE):
            national = value[1 + len(NIGERIA_CODE) :]
            if national.startswith("0"):  # +2340803... is a common typo
                national = national[1:]
            return _nigerian(national)
        if not _E164.match(value):
            raise InvalidPhoneNumber("Enter the number with its country code, e.g. +2348031234567")
        return value

    if not value.isdigit():
        raise InvalidPhoneNumber("Phone number can only contain digits")
    if value.startswith(NIGERIA_CODE) and len(value) == 13:
        return _nigerian(value[3:])
    if value.startswith("0") and len(value) == 11:
        return _nigerian(value[1:])
    if len(value) == 10:
        return _nigerian(value)
    raise InvalidPhoneNumber(
        "Enter a Nigerian number like 08031234567, or an international number with its + code"
    )


def _nigerian(national: str) -> str:
    if not _NG_NATIONAL.match(national):
        raise InvalidPhoneNumber("That is not a valid Nigerian mobile number")
    return f"+{NIGERIA_CODE}{national}"


def mask_phone(phone: str) -> str:
    """+2348031234567 -> +234803***4567, for logs and responses."""
    if len(phone) < 8:
        return "***"
    return f"{phone[:7]}***{phone[-4:]}"
