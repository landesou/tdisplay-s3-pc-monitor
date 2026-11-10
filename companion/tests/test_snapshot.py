"""JSON 契约：字段名、单位、取整、紧凑分隔符、null 降级、不发 src。"""

import json

from pcmond.collectors import RawSample
from pcmond.gpu import GpuSample
from pcmond.snapshot import build_snapshot, to_json


def make_sample(**overrides):
    values = dict(
        hostname="LENOVO-83NN",
        uptime_s=123456,
        cpu_avg=23.44,
        cpu_mhz=2400,
        cpu_cores=[12, 34, 5, 0],
        mem_pct=61.24,
        mem_used_mib=9340,
        mem_total_mib=15288,
        net_down=1.2345,
        net_up=0.3,
        disk_read=0.4,
        disk_write=2.1,
        gpu=GpuSample(
            name="NVIDIA GeForce RTX 5060 Laptop GPU",
            util=16,
            temp=58,
            vram_used=1864,
            vram_total=8151,
            power=29.16,
        ),
    )
    values.update(overrides)
    return RawSample(**values)


def test_contract_top_level_fields():
    snapshot = build_snapshot(make_sample(), seq=42, now=1762761600.9)
    assert set(snapshot) == {"v", "seq", "t", "host", "up", "cpu", "mem", "gpu", "net", "disk"}
    assert "src" not in snapshot  # 由设备填写，PC 端不发


def test_contract_values_and_units():
    snapshot = build_snapshot(make_sample(), seq=42, now=1762761600.9)
    assert snapshot["v"] == 1
    assert snapshot["seq"] == 42
    assert snapshot["t"] == 1762761600  # unix 秒取整
    assert snapshot["host"] == "LENOVO-83NN"
    assert snapshot["up"] == 123456
    # % 保留 1 位；MHz 取整；temp 恒为 null
    assert snapshot["cpu"] == {"avg": 23.4, "mhz": 2400, "temp": None, "cores": [12, 34, 5, 0]}
    # MiB 取整；pct 1 位
    assert snapshot["mem"] == {"pct": 61.2, "used": 9340, "total": 15288}
    assert snapshot["gpu"] == {
        "name": "NVIDIA GeForce RTX 5060 Laptop GPU",
        "util": 16,
        "temp": 58,
        "vram_used": 1864,
        "vram_total": 8151,
        "power": 29.2,
        "mhz": None,
    }
    # 速率 MiB/s 保留 2 位
    assert snapshot["net"] == {"down": 1.23, "up": 0.3}
    assert snapshot["disk"] == {"read": 0.4, "write": 2.1}


def test_gpu_null_when_nvidia_smi_unavailable():
    snapshot = build_snapshot(make_sample(gpu=None), seq=1)
    assert snapshot["gpu"] is None


def test_host_truncated_to_15_chars():
    snapshot = build_snapshot(make_sample(hostname="A-VERY-LONG-HOSTNAME-0123456789"), seq=1)
    assert snapshot["host"] == "A-VERY-LONG-HOS"
    assert len(snapshot["host"]) == 15


def test_json_is_compact_single_line_and_under_600_bytes():
    snapshot = build_snapshot(make_sample(cpu_cores=list(range(32))), seq=1, now=1762761600)
    line = to_json(snapshot)
    assert "\n" not in line
    assert line == json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    assert len(line.encode("utf-8")) <= 600


def test_json_keeps_utf8_hostname():
    snapshot = build_snapshot(make_sample(hostname="联想本本"), seq=1)
    line = to_json(snapshot)
    assert "联想本本" in line  # ensure_ascii=False，UTF-8 直出
