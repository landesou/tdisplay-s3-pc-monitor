"""nvidia-smi 输出解析：真实行、N/A、虚拟适配器、命令不存在、超时、非零退出。"""

import subprocess

import pytest

from pcmond import gpu

SMI_LINE = "NVIDIA GeForce RTX 5060 Laptop GPU, 16, 58, 1864, 8151, 29.15\n"
SMI_NA_LINE = "NVIDIA GeForce RTX 5060 Laptop GPU, N/A, N/A, N/A, N/A, N/A\n"


def test_parse_real_line():
    sample = gpu.parse_nvidia_smi(SMI_LINE)
    assert sample is not None
    assert sample.name == "NVIDIA GeForce RTX 5060 Laptop GPU"
    assert sample.util == 16
    assert sample.temp == 58
    assert sample.vram_used == 1864
    assert sample.vram_total == 8151
    assert sample.power == pytest.approx(29.15)


def test_parse_na_fields_become_none_not_crash():
    sample = gpu.parse_nvidia_smi(SMI_NA_LINE)
    assert sample is not None
    assert sample.name == "NVIDIA GeForce RTX 5060 Laptop GPU"
    assert sample.util is None
    assert sample.temp is None
    assert sample.vram_used is None
    assert sample.vram_total is None
    assert sample.power is None


def test_parse_skips_virtual_adapter_and_picks_nvidia():
    output = (
        "ToDesk Virtual Display, 1, 2, 3, 4, 5\n"
        "ExtensoDesk Virtual Display, 0, 0, 0, 0, 0\n"
        "NVIDIA GeForce RTX 5060 Laptop GPU, 16, 58, 1864, 8151, 29.50\n"
    )
    sample = gpu.parse_nvidia_smi(output)
    assert sample is not None
    assert "RTX 5060" in sample.name
    assert sample.power == pytest.approx(29.50)


def test_parse_na_name_only_returns_none():
    assert gpu.parse_nvidia_smi("N/A, N/A, N/A, N/A, N/A, N/A\n") is None


def test_parse_empty_and_garbage_return_none():
    assert gpu.parse_nvidia_smi("") is None
    assert gpu.parse_nvidia_smi("not,a,valid,row\n") is None


def test_query_gpu_missing_command_returns_none(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    assert gpu.query_gpu() is None


def test_query_gpu_timeout_returns_none(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3.0)

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    assert gpu.query_gpu() is None


def test_query_gpu_nonzero_exit_returns_none(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 9, stdout="", stderr="driver not loaded")

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    assert gpu.query_gpu() is None


def test_query_gpu_success(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=SMI_LINE, stderr="")

    monkeypatch.setattr(gpu.subprocess, "run", fake_run)
    sample = gpu.query_gpu()
    assert sample is not None
    assert sample.util == 16


def test_is_nvidia_filters_virtual_names():
    assert gpu.is_nvidia("NVIDIA GeForce RTX 5060 Laptop GPU")
    assert not gpu.is_nvidia("ToDesk Virtual Display")
    assert not gpu.is_nvidia("N/A")
