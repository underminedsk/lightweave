from __future__ import annotations

import time


class PySerialTransport:
    def __init__(self, port: str, baud: int = 115200, reset_on_open: bool = False) -> None:
        import serial

        self._serial = serial.Serial(port, baud, timeout=0.1, write_timeout=2.0)
        if not reset_on_open:
            time.sleep(0.2)
            self._serial.setDTR(False)
            self._serial.setRTS(False)
        time.sleep(2.0)
        self._serial.reset_input_buffer()

    def write_line(self, line: str) -> None:
        self._serial.write((line.rstrip("\r\n") + "\n").encode("utf-8"))

    def read_line(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        self._serial.timeout = 0
        while time.monotonic() < deadline:
            waiting = self._serial.in_waiting
            if waiting:
                chunk = self._serial.read(1)
                if not chunk:
                    continue
                buf.extend(chunk)
                if chunk in {b"\n", b"\r"}:
                    return buf.decode("utf-8", errors="replace")
            else:
                time.sleep(0.005)
        if buf:
            return buf.decode("utf-8", errors="replace")
        return None

    def close(self) -> None:
        self._serial.close()
