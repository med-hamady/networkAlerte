"""
Unit tests for ssh_service pure helpers — no network, no paramiko transport.

Covers _parse_board_model, which extracts the hardware model from an airOS
/etc/board.info dump so an airMAX LR mis-inferred as the wrong variant (M5 vs
5AC) self-heals from the device itself. Board.info is the only model source for
airOS-M LRs (M5), which do not answer the HTTP status.cgi the AC firmware does.
"""

from app.services.ssh_service import _parse_board_model


def test_parse_board_model_m5():
    board_info = (
        "board.sysid=0xe835\n"
        "board.name=LiteBeam M5\n"
        "board.shortname=LBE-M5\n"
        "board.hwaddr=DC:9F:DB:00:00:00\n"
    )
    assert _parse_board_model(board_info) == "LiteBeam M5"


def test_parse_board_model_5ac():
    board_info = (
        "board.sysid=0xe7b5\n"
        "board.name=LiteBeam 5AC Gen2\n"
        "board.shortname=LBE-5AC-Gen2\n"
    )
    assert _parse_board_model(board_info) == "LiteBeam 5AC Gen2"


def test_parse_board_model_shortname_fallback():
    # No board.name → falls back to board.shortname.
    assert _parse_board_model("board.shortname=LBE-M5\n") == "LBE-M5"


def test_parse_board_model_empty_or_garbage():
    assert _parse_board_model("") is None
    assert _parse_board_model("no equals signs here") is None
    assert _parse_board_model("board.other=x\n") is None
