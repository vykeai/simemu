import json

from simemu import create


def test_list_watchos_device_types_filters_to_watch(monkeypatch):
    payload = {
        "devicetypes": [
            {"identifier": "phone", "name": "iPhone 17 Pro"},
            {"identifier": "watch", "name": "Apple Watch Series 10 (46mm)"},
            {"identifier": "tv", "name": "Apple TV 4K"},
        ]
    }

    monkeypatch.setattr(
        create.subprocess,
        "check_output",
        lambda *args, **kwargs: json.dumps(payload).encode(),
    )

    devices = create.list_watchos_device_types()

    assert [device.name for device in devices] == ["Apple Watch Series 10 (46mm)"]


def test_list_watchos_runtimes_filters_to_watchos(monkeypatch):
    payload = {
        "runtimes": [
            {"identifier": "ios-26-2", "name": "iOS 26.2", "isAvailable": True},
            {"identifier": "watch-26-2", "name": "watchOS 26.2", "isAvailable": True},
            {"identifier": "watch-25-0", "name": "watchOS 25.0", "isAvailable": False},
        ]
    }

    monkeypatch.setattr(
        create.subprocess,
        "check_output",
        lambda *args, **kwargs: json.dumps(payload).encode(),
    )

    runtimes = create.list_watchos_runtimes()

    assert [(runtime.name, runtime.platform) for runtime in runtimes] == [("watchOS 26.2", "watchos")]
