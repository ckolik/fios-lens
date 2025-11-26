#!/usr/bin/env python3
"""Simple web viewer for router bandwidth history."""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from flask import Flask, jsonify, render_template

APP = Flask(__name__, static_folder="static", template_folder="templates")
LOGGER = logging.getLogger("bandwidth_viewer")
BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
SIZE_PATTERN = re.compile(r"^(?P<value>[0-9]*\.?[0-9]+)\s*(?P<unit>[a-zA-Z]+)?$")


@dataclass
class Sample:
    timestamp: datetime
    upload_bytes: float
    download_bytes: float
    ip_address: str


@APP.route("/")
def index() -> str:
    return render_template("index.html")


@APP.route("/api/bandwidth")
def api_bandwidth():
    devices = build_throughput_series()
    response = {"last_updated": datetime.utcnow().isoformat() + "Z", "devices": devices}
    return jsonify(response)


def build_throughput_series() -> List[Dict[str, object]]:
    entries = load_bandwidth_logs()
    grouped: Dict[Tuple[str, str], List[Sample]] = defaultdict(list)
    for name, ip, timestamp, upload, download in entries:
        key = (name or ip or "Unknown", ip)
        grouped[key].append(Sample(timestamp=timestamp, upload_bytes=upload, download_bytes=download, ip_address=ip))

    devices_output: List[Dict[str, object]] = []
    for (name, ip), samples in grouped.items():
        samples.sort(key=lambda sample: sample.timestamp)
        series = []
        prev = None
        for sample in samples:
            if prev is None:
                prev = sample
                continue
            delta_seconds = (sample.timestamp - prev.timestamp).total_seconds()
            if delta_seconds <= 0:
                prev = sample
                continue
            delta_upload = sample.upload_bytes - prev.upload_bytes
            delta_download = sample.download_bytes - prev.download_bytes
            if delta_upload < 0 or delta_download < 0:
                prev = sample
                continue
            upload_mbps = bytes_per_second_to_mbps(delta_upload / delta_seconds)
            download_mbps = bytes_per_second_to_mbps(delta_download / delta_seconds)
            series.append(
                {
                    "timestamp": sample.timestamp.isoformat(),
                    "upload_mbps": upload_mbps,
                    "download_mbps": download_mbps,
                }
            )
            prev = sample

        if series:
            devices_output.append(
                {
                    "device_name": name,
                    "ip_address": ip,
                    "series": series,
                }
            )

    devices_output.sort(key=lambda entry: entry["device_name"].lower())
    return devices_output


def load_bandwidth_logs() -> List[Tuple[str, str, datetime, float, float]]:
    entries: List[Tuple[str, str, datetime, float, float]] = []
    if not OUTPUT_DIR.exists():
        LOGGER.warning("Output directory %s does not exist", OUTPUT_DIR)
        return entries

    for path in sorted(OUTPUT_DIR.glob("device_bandwidth_*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception as err:  # pylint: disable=broad-except
            LOGGER.warning("Failed to read %s: %s", path, err)
            continue

        collected_at = data.get("collected_at")
        try:
            timestamp = datetime.fromisoformat(collected_at)
        except Exception:  # pylint: disable=broad-except
            LOGGER.warning("Invalid timestamp in %s", path)
            continue

        for device in data.get("lan_devices", []):
            name = (device.get("device_name") or "").strip()
            ip_address = (device.get("ip_address") or "").strip()
            upload = parse_size(device.get("upload_1hr", 0))
            download = parse_size(device.get("download_1hr", 0))
            entries.append((name, ip_address, timestamp, upload, download))

    return entries


def parse_size(raw) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return 0.0
    if text.isdigit():
        return float(text)
    match = SIZE_PATTERN.match(text)
    if not match:
        return 0.0
    value = float(match.group("value"))
    unit = (match.group("unit") or "bytes").lower()
    multiplier = {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kb": 1024,
        "kilobytes": 1024,
        "mb": 1024 ** 2,
        "megabytes": 1024 ** 2,
        "gb": 1024 ** 3,
        "gigabytes": 1024 ** 3,
    }.get(unit, 1)
    return value * multiplier


def bytes_per_second_to_mbps(value: float) -> float:
    return round((value * 8) / 1_000_000, 4)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    APP.run(debug=True)
