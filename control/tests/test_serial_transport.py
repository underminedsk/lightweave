from __future__ import annotations

import sys
import types

import control.serial_transport as serial_transport
from control.serial_transport import PySerialTransport


class FakeSerial:
    def __init__(self, port: str, baud: int, timeout: float, write_timeout: float) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.writes: list[bytes] = []
        self.dtr = True
        self.rts = True
        self.buffer = bytearray(b"diag\n" + b'{"id":1,"ok":true}\n')
        self.closed = False
        self.reset_count = 0

    @property
    def in_waiting(self) -> int:
        return len(self.buffer)

    def setDTR(self, value: bool) -> None:
        self.dtr = value

    def setRTS(self, value: bool) -> None:
        self.rts = value

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        self.reset_count += 1

    def read(self, size: int = 1) -> bytes:
        if not self.buffer:
            return b""
        chunk = bytes(self.buffer[:size])
        del self.buffer[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


def test_pyserial_transport_writes_lines_and_deasserts_reset(monkeypatch) -> None:
    created: list[FakeSerial] = []

    def serial_factory(port: str, baud: int, timeout: float, write_timeout: float) -> FakeSerial:
        serial = FakeSerial(port, baud, timeout, write_timeout)
        created.append(serial)
        return serial

    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=serial_factory))
    monkeypatch.setattr(serial_transport.time, "sleep", lambda _seconds: None)

    transport = PySerialTransport("/dev/cu.test", baud=57600)
    transport.write_line('{"id":1}')

    assert created[0].port == "/dev/cu.test"
    assert created[0].baud == 57600
    assert created[0].write_timeout == 2.0
    assert created[0].dtr is False
    assert created[0].rts is False
    assert created[0].reset_count == 1
    assert created[0].writes == [b'{"id":1}\n']
    assert transport.read_line(0.1) == "diag\n"

    transport.close()
    assert created[0].closed is True
