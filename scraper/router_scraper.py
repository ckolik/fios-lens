#!/usr/bin/env python3
"""
Router device scraper that uses Selenium for navigation and BeautifulSoup for parsing.
"""
from __future__ import annotations

import argparse
import configparser
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

LOGGER = logging.getLogger("router_scraper")

LABEL_TO_KEY = {
    "connection": "connection",
    "connection type": "connection_type",
    "frequency": "frequency",
    "protocol supported": "protocol_supported",
    "encryption": "encryption",
    "radio configuration": "radio_configuration",
    "phy rate / modulation rate": "phy_rate_modulation_rate",
    "rssi": "rssi",
    "snr": "snr",
    "mac address": "mac_address",
    "connected to": "connected_to",
    "ipv4 address": "ipv4_address",
    "subnet mask": "subnet_mask",
    "ipv4 dns": "ipv4_dns",
    "ipv4 address allocation": "ipv4_address_allocation",
    "lease type": "lease_type",
    "dhcp lease time remaining": "dhcp_lease_time_remaining",
    "ipv6 dns": "ipv6_dns",
    "network connection": "network_connection",
    "time on the network": "time_on_the_network",
    "port forwarding": "port_forwarding",
    "access control": "access_control",
    "dmz host": "dmz_host",
    "dns server": "dns_server",
}


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    if debug:
        LOGGER.debug("Debug logging enabled")


def normalize_label(label: str) -> str:
    return " ".join(label.strip().lower().split())


def clean_text(node) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Verizon router device inventory.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.ini"),
        help="Path to configuration .ini file.",
    )
    parser.add_argument(
        "--password",
        help="Override password defined in the config.ini file.",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force Selenium headless mode on/off (overrides config).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override the output directory.",
    )
    parser.add_argument(
        "--driver-path",
        type=Path,
        help="Path to a ChromeDriver binary (overrides config).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait after navigation before scraping HTML.",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> Dict[str, str]:
    parser = configparser.ConfigParser()
    if config_path.exists():
        parser.read(config_path)
    else:
        LOGGER.warning("Config file %s not found; falling back to defaults.", config_path)

    cfg = parser["router"] if "router" in parser else {}
    base_dir = config_path.parent

    def _resolve_path(raw: Optional[str], default: Path) -> Path:
        if not raw:
            return default
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        return candidate

    default_output = (base_dir.parent / "output").resolve()

    return {
        "url": cfg.get("url", "https://192.168.1.1").rstrip("/"),
        "password": cfg.get("password", ""),
        "headless": cfg.get("headless", "true"),
        "output_dir": str(_resolve_path(cfg.get("output_dir"), default_output)),
        "driver_path": str(_resolve_path(cfg.get("driver_path"), Path("")))
        if cfg.get("driver_path")
        else "",
    }


def build_driver(headless: bool, driver_path: Optional[Path]) -> webdriver.Chrome:
    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-insecure-localhost")
    options.set_capability("acceptInsecureCerts", True)
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    service = Service(executable_path=str(driver_path)) if driver_path else None
    try:
        driver = webdriver.Chrome(options=options, service=service)
    except WebDriverException as err:
        LOGGER.error("Unable to start ChromeDriver: %s", err)
        raise
    return driver


@dataclass
class DeviceRecord:
    name: str
    connection: str
    host: str
    mac_address: str
    parental_controls: str
    detail_url: str

    @classmethod
    def from_row(cls, row, base_url: str) -> Optional["DeviceRecord"]:
        cells = row.select('div[role="cell"]')
        if len(cells) < 5:
            return None

        name = clean_text(cells[0])
        connection = clean_text(cells[1])
        host = clean_text(cells[2])
        mac_address = clean_text(cells[3]).lower()
        parental_controls = clean_text(cells[4]) or "None"

        link = row.select_one('a[href*="settings/"]')
        href = link["href"] if link and link.has_attr("href") else ""
        detail_url = urljoin(base_url + "/", href) if href else f"{base_url}/#/adv/devices/list/settings/{mac_address}"

        return cls(
            name=name,
            connection=connection,
            host=host,
            mac_address=mac_address,
            parental_controls=parental_controls,
            detail_url=detail_url,
        )


class RouterScraper:
    def __init__(
        self,
        base_url: str,
        password: str,
        headless: bool,
        output_dir: Path,
        driver_path: Optional[Path],
        delay: float,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.output_dir = output_dir
        self.delay = delay
        self.driver = build_driver(headless=headless, driver_path=driver_path)
        self.wait = WebDriverWait(self.driver, timeout)

    def close(self) -> None:
        try:
            self.driver.quit()
        except WebDriverException:
            LOGGER.debug("Driver already closed.")

    def login(self) -> None:
        login_url = f"{self.base_url}/#/login/"
        LOGGER.info("Navigating to login page %s", login_url)
        self.driver.get(login_url)
        try:
            password_field = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]'))
            )
        except TimeoutException as err:
            LOGGER.error("Password field did not load: %s", err)
            raise

        password_field.clear()
        password_field.send_keys(self.password)
        LOGGER.debug("Password entered, attempting to submit login form.")
        try:
            login_button = self.driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Log In"]')
        except NoSuchElementException as err:
            LOGGER.error("Unable to locate Log In button: %s", err)
            raise
        login_button.click()

        try:
            self.wait.until(EC.presence_of_element_located((By.ID, "navigation_bar")))
            LOGGER.info("Login successful.")
        except TimeoutException:
            LOGGER.warning("Navigation bar did not appear; continuing assuming existing session.")

    def load_device_rows(self) -> List[DeviceRecord]:
        devices_url = f"{self.base_url}/#/adv/devices/list"
        LOGGER.info("Loading device list from %s", devices_url)
        self.driver.get(devices_url)
        try:
            self.wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.row.wifi-row"))
            )
        except TimeoutException as err:
            LOGGER.error("Device list did not load: %s", err)
            raise

        seen: Dict[str, DeviceRecord] = {}
        stagnation = 0
        max_scrolls = 60
        for _ in range(max_scrolls):
            added = self._capture_visible_rows(seen)
            if added == 0:
                stagnation += 1
            else:
                stagnation = 0

            self.driver.execute_script("window.scrollBy(0, window.innerHeight);")
            time.sleep(self.delay)

            if stagnation >= 5:
                break

        self._capture_visible_rows(seen)
        LOGGER.info("Parsed %d devices from the list view.", len(seen))
        return list(seen.values())

    def _capture_visible_rows(self, seen: Dict[str, DeviceRecord]) -> int:
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        added = 0
        for row in soup.select("div.row.wifi-row"):
            record = DeviceRecord.from_row(row, self.base_url)
            if not record:
                continue
            if record.mac_address in seen:
                continue
            seen[record.mac_address] = record
            added += 1
        return added

    def collect_device_details(self, url: str, attempts: int = 2) -> Dict[str, str]:
        """Fetch the device detail page, retrying if the DOM never materializes."""

        last_details: Dict[str, str] = {}
        for attempt in range(1, attempts + 1):
            details = self._scrape_device_details_once(url)
            if details:
                return details

            last_details = details
            if attempt < attempts:
                if self._on_login_page():
                    LOGGER.info("Re-authenticating after being redirected to the login page.")
                    self.login()
                LOGGER.debug(
                    "Retrying device detail page %s (%d/%d)", url, attempt + 1, attempts
                )
                time.sleep(self.delay)

        if not last_details:
            LOGGER.warning("Detail page %s yielded no data after %d attempts.", url, attempts)
        return last_details

    def _scrape_device_details_once(self, url: str) -> Dict[str, str]:
        LOGGER.debug("Loading device details page %s", url)
        self.driver.get(url)
        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.section-title")))
        except TimeoutException as err:
            LOGGER.warning("Timed out waiting for detail page %s: %s", url, err)
            return {}

        time.sleep(self.delay)
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        details: Dict[str, str] = {}

        status_span = soup.select_one(".icon-dev-bg-on span, .icon-dev-bg-off span")
        if status_span:
            details["status"] = clean_text(status_span)

        type_span = soup.select_one("span.dev-type span.dev-type") or soup.select_one("span.dev-type")
        if type_span:
            details["device_type"] = clean_text(type_span)

        details.update(extract_make_model_os(soup))
        details.update(extract_label_value_pairs(soup))
        return details

    def _on_login_page(self) -> bool:
        try:
            current_url = self.driver.current_url
        except WebDriverException:
            return False

        if "login" in current_url.lower():
            return True

        try:
            self.driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            return True
        except NoSuchElementException:
            return False

    def scrape(self) -> Dict[str, object]:
        self.login()
        device_rows = self.load_device_rows()
        if not device_rows:
            raise RuntimeError("No devices were discovered on the device list page.")

        run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        collected_at = datetime.now(timezone.utc).isoformat()

        devices_output = []
        for index, record in enumerate(device_rows, start=1):
            LOGGER.info("Collecting detail page for %s (%d/%d)", record.mac_address, index, len(device_rows))
            try:
                detail_data = self.collect_device_details(record.detail_url)
            except WebDriverException as err:
                LOGGER.warning("Failed to load detail page for %s: %s", record.mac_address, err)
                detail_data = {}

            connection = derive_connection(record.connection, detail_data.get("connection"))
            status = derive_status(detail_data.get("status"), connection)

            merged = {
                "name": record.name,
                "connection": connection,
                "mac_address": record.mac_address,
                "host": record.host,
                "parental_controls": record.parental_controls or detail_data.get("parental_controls", "None"),
                "status": status,
                "run_id": run_id,
                "collected_at": collected_at,
            }

            for key, value in detail_data.items():
                if key == "connection":
                    continue
                if value is None:
                    continue
                merged[key] = value

            devices_output.append(merged)

        payload = {
            "run_id": run_id,
            "collected_at": collected_at,
            "device_count": len(devices_output),
            "devices": devices_output,
        }
        return payload


def derive_connection(primary: str, detail: Optional[str]) -> str:
    candidate = detail or primary or ""
    candidate = candidate.strip()
    if "/" in candidate:
        candidate = candidate.split("/", 1)[-1].strip()
    return candidate


def derive_status(detail_status: Optional[str], connection: str) -> str:
    if detail_status:
        return detail_status
    if connection and "offline" in connection.lower():
        return "Offline"
    if connection:
        return "Online"
    return "Unknown"


def extract_make_model_os(soup: BeautifulSoup) -> Dict[str, str]:
    results: Dict[str, str] = {}
    label = soup.find("div", string=lambda value: value and "Make, Model" in value)
    if not label:
        return results
    current_row = label.find_parent("div", class_="row")
    if not current_row:
        return results

    next_row = current_row.find_next_sibling("div", class_="row")
    collected = []
    while next_row:
        heading = next_row.select_one("div.dev-class")
        if heading and heading.get_text(strip=True).lower().startswith("host"):
            break
        value_cell = next_row.select_one("div.col-4.dev-info")
        if value_cell:
            collected.append(clean_text(value_cell))
        if len(collected) >= 3:
            break
        next_row = next_row.find_next_sibling("div", class_="row")

    mapping = ["device_make", "device_model", "device_operating_system"]
    for key, value in zip(mapping, collected):
        results[key] = value
    return results


def extract_label_value_pairs(soup: BeautifulSoup) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for heading in soup.select('div.inner-row div[role="heading"][aria-level="4"].gray6'):
        label = normalize_label(heading.get_text())
        key = LABEL_TO_KEY.get(label)
        if not key:
            continue
        value_node = heading.find_next_sibling("div")
        values[key] = clean_text(value_node)
    return values


def write_output(payload: Dict[str, object], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"devices_{timestamp}.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    LOGGER.info("Wrote %d devices to %s", payload["device_count"], output_path)
    return output_path


def main() -> int:
    args = parse_args()
    configure_logging(args.debug)
    config = load_config(args.config)

    password = args.password or config.get("password", "")
    if not password:
        LOGGER.error("Router password must be provided via config or --password.")
        return 1

    headless_cfg = True
    config_headless = config.get("headless")
    if config_headless is not None:
        headless_cfg = str(config_headless).lower() in {"1", "true", "yes", "on"}
    if args.headless is not None:
        headless_cfg = args.headless

    output_dir = args.output_dir or Path(config["output_dir"])
    driver_path = args.driver_path or (Path(config["driver_path"]) if config.get("driver_path") else None)

    scraper = RouterScraper(
        base_url=config.get("url", "https://192.168.1.1"),
        password=password,
        headless=headless_cfg,
        output_dir=output_dir,
        driver_path=driver_path,
        delay=args.delay,
    )

    try:
        payload = scraper.scrape()
        write_output(payload, output_dir)
    except KeyboardInterrupt:
        LOGGER.warning("Scraper interrupted by user.")
        return 1
    except Exception:
        LOGGER.exception("Scraper failed.")
        return 1
    finally:
        scraper.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
