"""USB 链路：端口发现（VID/描述/排除）、hello → cfg 下发（契约 §4）、串口异常不杀线程。"""

import json
import threading
import time
from types import SimpleNamespace

import serial

from pcmond import usb_link
from pcmond.config import Config
from pcmond.usb_link import UsbLink, find_port


def fake_port(device, vid=None, pid=None, description="", manufacturer="", hwid=""):
    return SimpleNamespace(
        device=device, vid=vid, pid=pid, description=description, manufacturer=manufacturer, hwid=hwid
    )


class FakePort:
    """只记录写入的假串口。"""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)
        return len(data)

    def flush(self):
        pass


class FakeSampler:
    def latest(self):
        return None


def test_override_wins():
    assert find_port("COM9", ports=[]) == "COM9"


def test_finds_esp32_by_vid():
    ports = [fake_port("COM3", description="标准串口"), fake_port("COM7", vid=0x303A, description="USB 串行设备 (COM7)")]
    assert find_port(ports=ports) == "COM7"


def test_finds_esp32_by_description_when_vid_unknown():
    ports = [fake_port("COM4", description="USB 串行设备 (COM4)")]
    assert find_port(ports=ports) == "COM4"


def test_vid_beats_description():
    ports = [
        fake_port("COM4", description="USB 串行设备 (COM4)"),
        fake_port("COM7", vid=0x303A, description="USB JTAG/serial debug unit"),
    ]
    assert find_port(ports=ports) == "COM7"


def test_no_esp32_returns_none():
    ports = [fake_port("COM1", description="蓝牙链接上的标准串行 (COM1)")]
    assert find_port(ports=ports) is None


def test_prefers_usb_serial_jtag_pid_1001():
    """本机板子是 303A:1001（内置 USB-Serial/JTAG）：有多个 ESP32 设备时优先抓它。"""
    ports = [
        fake_port("COM3", vid=0x303A, pid=0x0002, description="USB 串行设备 (COM3)"),
        fake_port("COM7", vid=0x303A, pid=0x1001, description="USB JTAG/serial debug unit"),
    ]
    assert find_port(ports=ports) == "COM7"


def test_accepts_other_esp32_pid_when_no_1001():
    """没有 1001 时仍接受同 VID 的 TinyUSB CDC（303A:0002），不能因为 PID 不同就抓不到。"""
    ports = [fake_port("COM3", vid=0x303A, pid=0x0002, description="USB 串行设备")]
    assert find_port(ports=ports) == "COM3"


def test_pid_1001_beats_vid_match_without_pid():
    ports = [
        fake_port("COM5", vid=0x303A, description="USB 串行设备 (COM5)"),
        fake_port("COM7", vid=0x303A, pid=0x1001, description="USB JTAG/serial debug unit"),
    ]
    assert find_port(ports=ports) == "COM7"


def test_cfg_frame_uses_nvs_keys(monkeypatch):
    monkeypatch.setattr("pcmond.usb_link.lan_ip", lambda: "192.168.1.10")
    config = Config(token="tok-123", port=8787, wifi_ssid="MyWiFi", wifi_pass="pw123456")
    link = UsbLink(FakeSampler(), config)
    payload = link._build_cfg()
    assert payload is not None
    assert payload["t"] == "cfg"
    assert payload["wifi_ssid"] == "MyWiFi"
    assert payload["wifi_pass"] == "pw123456"
    assert payload["api_token"] == "tok-123"
    assert payload["api_url"] == "http://192.168.1.10:8787/api/stats"


def test_cfg_skipped_without_wifi_credentials():
    link = UsbLink(FakeSampler(), Config(token="tok"))
    assert link._build_cfg() is None


def test_hello_need_cfg_writes_one_cfg_line(monkeypatch):
    monkeypatch.setattr("pcmond.usb_link.lan_ip", lambda: "10.0.0.2")
    link = UsbLink(FakeSampler(), Config(token="tok", wifi_ssid="S", wifi_pass="P"))
    port = FakePort()
    link._handle_device_line(port, b'{"t":"hello","need_cfg":1}\n')
    assert len(port.written) == 1
    payload = json.loads(port.written[0].decode("utf-8"))
    assert payload["t"] == "cfg"
    assert payload["wifi_ssid"] == "S"
    assert port.written[0].endswith(b"\n")


def test_hello_without_need_cfg_writes_nothing():
    link = UsbLink(FakeSampler(), Config(token="tok"))
    port = FakePort()
    link._handle_device_line(port, b'{"t":"hello","need_cfg":0}\n')
    assert port.written == []


def test_non_json_device_output_is_ignored():
    link = UsbLink(FakeSampler(), Config(token="tok"))
    port = FakePort()
    link._handle_device_line(port, b"ets Jun  8 2016 00:22:57\r\n")
    assert port.written == []


def test_connected_tracks_port_name():
    link = UsbLink(FakeSampler(), Config(token="tok"))
    assert link.connected is False
    link.port_name = "COM7"
    assert link.connected is True


def test_serial_open_failure_does_not_kill_thread(monkeypatch):
    """串口被别的程序占用/拔掉：线程进日志并退避重连，不把进程带崩。"""
    attempts = {"count": 0}

    def failing_serial(*args, **kwargs):
        attempts["count"] += 1
        raise serial.SerialException("拒绝访问（端口被占用）")

    monkeypatch.setattr(usb_link, "find_port", lambda override=None: "COM9")
    monkeypatch.setattr(usb_link.serial, "Serial", failing_serial)

    link = UsbLink(FakeSampler(), Config(token="t"))
    link.start()
    time.sleep(0.15)
    try:
        assert link._thread is not None and link._thread.is_alive()
        assert attempts["count"] >= 1
    finally:
        link.stop()
    assert not link._thread.is_alive()


def test_usb_rate_is_clamped_to_contract_range():
    low = UsbLink(FakeSampler(), Config(token="t", usb_hz=0.1))
    high = UsbLink(FakeSampler(), Config(token="t", usb_hz=9.0))
    assert low._interval_s == 1.0  # 1 Hz 下限
    assert high._interval_s == 0.5  # 2 Hz 上限


class SnapshotSampler:
    """给 _pump 用的最小 sampler：永远有一帧可发。"""

    def latest(self):
        return {"v": 1, "seq": 7, "t": 1762761600}


class TimeoutPort(FakePort):
    """写超时（设备端没读走数据）的假串口。"""

    def __init__(self):
        super().__init__()
        self.in_waiting = 0

    def write(self, data):
        raise serial.SerialTimeoutException("Write timeout")


def test_write_timeout_keeps_port_open_and_does_not_raise():
    """设备端不读 CDC 数据时写会超时——这不是拔线，不能每帧都重连。"""
    link = UsbLink(SnapshotSampler(), Config(token="t"))
    link._interval_s = 0.01
    port = TimeoutPort()
    thread = threading.Thread(target=link._pump, args=(port, "COM9"), daemon=True)
    thread.start()
    time.sleep(0.1)
    link._stop.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_port_name_cleared_before_reconnect_backoff(monkeypatch):
    """断线退避期间 port_name 必须已清空，否则托盘会把断线显示成绿色。"""

    class DyingPort:
        in_waiting = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, data):
            raise serial.SerialException("串口消失")

        def flush(self):
            pass

    monkeypatch.setattr(usb_link, "find_port", lambda override=None: "COM9")
    monkeypatch.setattr(usb_link.serial, "Serial", lambda *args, **kwargs: DyingPort())
    link = UsbLink(SnapshotSampler(), Config(token="t"))
    link._interval_s = 0.01
    thread = threading.Thread(target=link._run, daemon=True)
    thread.start()
    try:
        time.sleep(0.2)  # 第一次写失败 → 进入 0.5 s 退避
        assert link.port_name is None
    finally:
        link._stop.set()
        thread.join(timeout=2.0)
