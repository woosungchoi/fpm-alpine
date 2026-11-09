"""Bounded retries for idempotent upstream reads; never retry policy failures."""
import errno
import http.client
import socket
import time
import urllib.error


def transient(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 429, 500, 502, 503, 504}
    if isinstance(error, urllib.error.URLError):
        return transient(error.reason)
    return isinstance(error, (TimeoutError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected)) or (
        isinstance(error, OSError) and error.errno in {errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ENETUNREACH, socket.EAI_AGAIN}
    )


def read_with_retry(operation):
    for attempt in range(3):
        try:
            return operation()
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            if attempt == 2 or not transient(error):
                raise
            time.sleep(2 ** attempt)
