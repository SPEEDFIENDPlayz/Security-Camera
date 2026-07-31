import pytest

from app.setup.wizard import build_rtsp_url


def test_rtsp_url_encodes_credentials():
    assert build_rtsp_url("camera.local", "554", "admin", "p@ ss", "cam/main") == "rtsp://admin:p%40%20ss@camera.local:554/cam/main"


@pytest.mark.parametrize("port", ["", "0", "65536", "not-a-port"])
def test_rtsp_url_rejects_bad_port(port):
    with pytest.raises(ValueError):
        build_rtsp_url("camera.local", port, "admin", "password", "main")


def test_rtsp_url_requires_all_main_stream_fields():
    with pytest.raises(ValueError):
        build_rtsp_url("", "554", "admin", "password", "main")
