"""Generate an API key and the hash to configure.

    uv run python -m trialsentinel.api.keys

The key is shown once: give it to the client. Only the hash goes into TS_API_KEYS_SHA256."""

from trialsentinel.api.security import hash_key, new_key


def main() -> int:
    key = new_key()
    print(f"API key (store it securely; it is not saved anywhere): {key}")
    print(f"SHA-256 to put in TS_API_KEYS_SHA256: {hash_key(key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
