from pathlib import Path


def test_service_installer_uses_private_review_port_8088():
    script = Path("install_services.sh").read_text(
        encoding="utf-8"
    )

    assert (
        "uvicorn review_app:app --host 0.0.0.0 --port 8088"
        in script
    )
    assert (
        "http://raspberrypi.local:8088"
        in script
    )
    assert (
        "uvicorn review_app:app --host 0.0.0.0 --port 8080"
        not in script
    )
