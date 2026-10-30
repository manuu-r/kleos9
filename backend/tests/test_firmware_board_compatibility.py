import base64

import pytest

from kleos.native_runner import UnsupportedTool, resolve_zephyr_board, run_job
from kleos.storage import digest


@pytest.mark.parametrize("requested,resolved", [
    ("nrf52840dk_nrf52840", "nrf52840dk/nrf52840"),
    ("nrf52840dk/nrf52840", "nrf52840dk/nrf52840"),
    ("native_sim/native/64", "native_sim/native/64"),
    ("nrf9160dk@0.14.0/nrf9160/ns", "nrf9160dk@0.14.0/nrf9160/ns"),
])
def test_explicit_zephyr_board_resolution(requested, resolved):
    assert resolve_zephyr_board(requested) == resolved


@pytest.mark.parametrize("board", [None, True, [], "", "-DBOARD_ROOT=/tmp", "/tmp/board", "../board",
    "foo/../bar", "foo//bar", "foo/", "foo;touch /tmp/pwn", "foo\\bar", "foo\nbar", "a" * 101])
def test_unsafe_or_invalid_board_targets_cannot_become_build_arguments(board):
    with pytest.raises(UnsupportedTool):
        resolve_zephyr_board(board)


def test_native_firmware_build_records_requested_and_resolved_board_without_peripheral_claims(tmp_path):
    source = b"cmake_minimum_required(VERSION 3.20.0)"
    request = {"schemaVersion": 1, "lane": "firmware", "purpose": "build", "timeoutSeconds": 30,
        "interfaces": {"firmware": {"board": "nrf52840dk_nrf52840"}},
        "inputArtifacts": [{"id": "source", "name": "CMakeLists.txt", "lane": "firmware", "sha256": digest(source),
                            "contentBase64": base64.b64encode(source).decode()}]}
    calls = []
    def execute(args, work, timeout):
        calls.append(args)
        output = work / "build/zephyr"
        output.mkdir(parents=True)
        (output / "zephyr.elf").write_bytes(b"fixture-elf")
        (output / "zephyr.bin").write_bytes(b"fixture-bin")
        return 0, "compiled"
    result = run_job(request, tmp_path, execute)
    assert result.status == "succeeded" and not result.checks
    assert calls == [["west", "build", "-b", "nrf52840dk/nrf52840", "-d", "build", "."]]
    assert result.measurements["zephyrBoard"] == {"requested": "nrf52840dk_nrf52840",
        "resolved": "nrf52840dk/nrf52840", "aliasApplied": True, "aliasPolicy": "zephyr-4.2-board-targets-v1"}
    assert any("does not establish custom PCB compatibility" in text for text in result.limitations)
