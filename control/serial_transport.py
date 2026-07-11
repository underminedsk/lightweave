from __future__ import annotations

import time


class SerialTransportError(RuntimeError):
    pass


class PySerialTransport:
    def __init__(self, port: str, baud: int = 115200, reset_on_open: bool = False) -> None:
        import serial

        self._serial_module = serial
        self._serial_errors = (OSError, serial.SerialException)
        self._port = port
        self._baud = baud
        self._reset_on_open = reset_on_open
        self._serial = self._open()

    def _open(self):
        connection = self._serial_module.Serial(
            self._port,
            self._baud,
            timeout=0.1,
            write_timeout=2.0,
        )
        if not self._reset_on_open:
            time.sleep(0.2)
            connection.setDTR(False)
            connection.setRTS(False)
        time.sleep(2.0)
        connection.reset_input_buffer()
        return connection

    def _reconnect(self) -> None:
        try:
            self._serial.close()
        except self._serial_errors:
            pass
        try:
            self._serial = self._open()
        except self._serial_errors as error:
            raise SerialTransportError(f"serial reconnect failed on {self._port}: {error}") from error

    def write_line(self, line: str) -> None:
        payload = (line.rstrip("\r\n") + "\n").encode("utf-8")
        try:
            self._serial.write(payload)
        except self._serial_errors:
            self._reconnect()
            try:
                self._serial.write(payload)
            except self._serial_errors as error:
                raise SerialTransportError(f"serial write failed on {self._port}: {error}") from error

    def read_line(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        try:
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
        except self._serial_errors:
            self._reconnect()
            return None
        if buf:
            return buf.decode("utf-8", errors="replace")
        return None

    def close(self) -> None:
        self._serial.close()
