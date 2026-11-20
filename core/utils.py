import re
import secrets
import string

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
PHONE_RE = re.compile(r"^09\d{9}$")


def normalize_phone(value: str) -> str:
    """Normalize Iranian mobile numbers to ``09XXXXXXXXX``. Raises ValueError."""
    if value is None:
        raise ValueError("Phone number is required")
    phone = re.sub(r"[\s\-()]", "", str(value).translate(_DIGITS))
    if phone.startswith("+98"):
        phone = "0" + phone[3:]
    elif phone.startswith("0098"):
        phone = "0" + phone[4:]
    elif phone.startswith("98") and len(phone) == 12:
        phone = "0" + phone[2:]
    elif phone.startswith("9") and len(phone) == 10:
        phone = "0" + phone
    if not PHONE_RE.match(phone):
        raise ValueError("Invalid Iranian mobile number")
    return phone


def random_code(length: int = 10, alphabet: str = string.ascii_uppercase + string.digits) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))
