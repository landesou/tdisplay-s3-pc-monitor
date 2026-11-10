"""命令行参数：覆盖只影响本次进程，且 --no-usb/--no-http/--interval 生效。"""

from pcmond.config import Config
from pcmond.main import apply_overrides, build_parser


def test_cli_overrides_are_applied():
    args = build_parser().parse_args(
        ["--port", "9090", "--token", "abc", "--serial", "COM7", "--no-usb", "--no-http", "--interval", "1.5"]
    )
    config = apply_overrides(Config(token="old"), args)
    assert config.port == 9090
    assert config.token == "abc"
    assert config.serial == "COM7"
    assert config.usb_enabled is False
    assert config.http_enabled is False
    assert config.interval_s == 1.5


def test_no_overrides_keep_config_values():
    args = build_parser().parse_args([])
    config = apply_overrides(Config(token="keep", port=8787), args)
    assert config.token == "keep"
    assert config.port == 8787
    assert config.usb_enabled is True
    assert config.http_enabled is True


def test_interval_has_floor():
    args = build_parser().parse_args(["--interval", "0.01"])
    assert apply_overrides(Config(token="t"), args).interval_s == 0.2


def test_no_tray_flag_disables_tray():
    args = build_parser().parse_args(["--no-tray"])
    config = apply_overrides(Config(token="t"), args)
    assert config.tray_enabled is False
    assert apply_overrides(Config(token="t"), build_parser().parse_args([])).tray_enabled is True
