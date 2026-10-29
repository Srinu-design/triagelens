# eval/code_cases.py — tickets that carry an error code: (title, description, category, expected master or None)
# Written before measuring; expected verdicts are in §8. Never edit them to make the rule look better.
from app.seed_data import B

CODE_CASES = [
    # same code, same bug
    ("Safari keeps logging us out with E_0X7F3A",
     "Our team on Safari is signed out every few minutes; the console shows E_0X7F3A.", B, "BUG-109"),
    ("Checkout fails at peak with ERR_PG_POOL_TIMEOUT_504",
     "During the evening rush, paying returns a 504 and the logs say ERR_PG_POOL_TIMEOUT_504.", B, "BUG-101"),
    ("Password reset mail bounced with SMTP_RELAY_421",
     "Reset emails are never delivered; the relay answers SMTP_RELAY_421.", B, "BUG-103"),
    # same code, different bug (another feature)
    ("Profile photo upload rejected with PAYLOAD_TOO_LARGE_413",
     "A 12 MB avatar fails to upload with PAYLOAD_TOO_LARGE_413.", B, None),
    ("Login times out with ERR_PG_POOL_TIMEOUT_504",
     "Signing in hangs and then fails with ERR_PG_POOL_TIMEOUT_504 on Monday mornings.", B, None),
    ("Report download fails with E_0X7F3A",
     "Clicking Download on the sales report shows a blank page and E_0X7F3A in the console.", B, None),
]
