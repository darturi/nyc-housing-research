"""Conservative admission bounds for the supported byte-tokenized profiles."""


def input_token_bound(text: str, *, answer: bool = False) -> int:
    # A byte can occupy a token on its own. The former bytes/4 average was not
    # an upper bound. Reserve framing space for the single Responses input.
    return max(1, len(text.encode("utf-8"))) + (64 if answer else 0)
