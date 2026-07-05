from __future__ import annotations

import subprocess

Import("env")


def _git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True).strip()
    except Exception:
        return ""


short = _git(["rev-parse", "--short=8", "HEAD"])
build_id = short if len(short) == 8 else "00000000"

dirty = "0"
if _git(["status", "--porcelain", "--", "include", "src", "platformio.ini", "scripts/firmware_build_id.py"]):
    dirty = "1"

env.Append(
    CPPDEFINES=[
        ("FIRMWARE_BUILD_ID", f"0x{build_id}u"),
        ("FIRMWARE_BUILD_DIRTY", dirty),
    ]
)
