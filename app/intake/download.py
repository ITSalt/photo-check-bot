"""Stream a Telegram file into a directory under a name the sender does not choose. T1d.

    download(get_file, fetch, file_id, dest_dir, max_bytes) -> path

`get_file(file_id)` is the Bot API's `getFile` result; `fetch(file_path)` is an
iterable of byte chunks. The file is written to a RANDOM name inside `dest_dir`
with owner-only permissions, and the path returned is absolute.

The cap is enforced twice, because the two numbers are different claims:

  * the DECLARED size (`file_size`) is checked before `fetch` is ever called, so a
    file the API already told us is too big costs no traffic at all;
  * the STREAMED size is counted as chunks arrive, because the declared size can be
    absent (`None`) or wrong, and a chunk is the only thing that actually spends
    quota.

On any failure -- over the cap, a broken stream, a write error -- the partial file
is removed before the exception leaves: a caller that catches `TooLarge` never has
to guess whether something is lying in `dest_dir`. The one thing this does NOT do
is decide what a picture is; it writes bytes and returns where they went.
"""
import os
import secrets

IMPLEMENTED = True


class TooLarge(Exception):
    """The file exceeds `max_bytes`, declared or as it arrived."""


def _new_path(dest_dir):
    """An absolute path in `dest_dir` that no file_id and no server path can name.

    Telegram's `file_path` is remote input, and `file_id` is remote input; a name
    built from either is how a download becomes a write wherever the sender
    likes. The name here is ours and only ours.
    """
    while True:
        name = "dl-" + secrets.token_hex(16) + ".bin"
        path = os.path.join(dest_dir, name)
        if not os.path.exists(path):
            return path


def download(get_file, fetch, file_id, dest_dir, max_bytes):
    info = get_file(file_id) or {}
    declared = info.get("file_size")
    if declared is not None and declared > max_bytes:
        raise TooLarge(
            "declared size %d exceeds the limit %d" % (declared, max_bytes))

    written = 0
    while True:
        path = _new_path(dest_dir)
        try:
            # O_EXCL closes both gaps at once: the file exists with 0o600 already
            # applied (nothing is ever readable by group or others), and a name
            # taken between the check and this call is refused rather than reused.
            # On Windows the mode bits are ignored and O_EXCL still refuses.
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        break
    handle = os.fdopen(fd, "wb")
    try:
        for chunk in fetch(info.get("file_path")):
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                raise TooLarge(
                    "streamed %d bytes exceeds the limit %d" % (written, max_bytes))
            handle.write(chunk)
    except BaseException:
        handle.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    handle.close()
    return os.path.abspath(path)
