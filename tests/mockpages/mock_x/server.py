"""Tiny local server for the mock X page. The shared implementation is in tests/mockpages/server.py.

    python -m tests.mockpages.mock_x.server
Then open the printed url + /mock_x/index.html?variant=<name>
"""
from tests.mockpages.server import FIXTURE, MockServer  # noqa: F401  (re-exported for tests)

if __name__ == "__main__":
    import time

    with MockServer() as s:
        print(f"{s.url}/mock_x/index.html", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
