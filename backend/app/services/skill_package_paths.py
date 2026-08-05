"""Path validation for untrusted Skill package ZIP members."""

from __future__ import annotations

import re


class SkillPackagePathError(ValueError):
    """A ZIP member name is not a safe relative POSIX path."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(path)


_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_FORBIDDEN_CHARS = set('<>"|?*')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "CONIN$",
    "CONOUT$",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"COM{suffix}" for suffix in ("¹", "²", "³")),
    *(f"LPT{index}" for index in range(1, 10)),
    *(f"LPT{suffix}" for suffix in ("¹", "²", "³")),
}


def validate_member_name(name: str, *, is_directory: bool = False) -> str:
    """Return *name* unchanged when it is a safe ZIP member name.

    ZIP names are deliberately not repaired or normalized: callers retain their
    exact UTF-8 spelling and separately reject ambiguous normalized spellings.
    """
    if not name or "\x00" in name or "\\" in name or ":" in name or name.startswith("/") or _DRIVE.match(name):
        raise SkillPackagePathError(name)
    candidate = name[:-1] if is_directory and name.endswith("/") else name
    if not candidate:
        raise SkillPackagePathError(name)
    segments = candidate.split("/")
    for segment in segments:
        device_alias = segment.split(".", 1)[0].rstrip(" .").upper()
        if (
            not segment
            or segment in {".", ".."}
            or any(character < " " or character in _WINDOWS_FORBIDDEN_CHARS for character in segment)
            or segment.endswith(" ")
            or segment.endswith(".")
            or device_alias in _WINDOWS_RESERVED_NAMES
        ):
            raise SkillPackagePathError(name)
    return candidate
