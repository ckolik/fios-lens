#!/usr/bin/env python3
"""Scrape WAN/LAN bandwidth metrics from the router monitoring UI."""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from router_scraper import build_driver, clean_text, configure_logging, load_config, normalize_label

LOGGER = logging.getLogger("bandwidth_scraper")
SIZE_PATTERN = re.compile(r"^(?P<value>[0-9]*\.?[0-9]+)\s*(?P<unit>[a-zA-Z]+)?$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape WAN/LAN bandwidth statistics from the router UI.")
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


class BandwidthScraper:
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

    def collect(self) -> Dict[str, object]:
        self.login()
        run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        collected_at = datetime.now(timezone.utc).isoformat()

        payload = self.collect_bandwidth_usage(run_id, collected_at)
        if not payload:
            raise RuntimeError("No bandwidth metrics were captured from the monitoring page.")
        return payload

    def collect_bandwidth_usage(self, run_id: str, collected_at: str) -> Dict[str, object]:
        bandwidth_url = f"{self.base_url}/#/adv/monitoring/bandwidth"
        LOGGER.info("Collecting bandwidth metrics from %s", bandwidth_url)
        try:
            self.driver.get(bandwidth_url)
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.cat-info")))
        except TimeoutException as err:
            LOGGER.warning("Bandwidth page did not load: %s", err)
            return {}

        time.sleep(self.delay)
        wan_metrics = self._scrape_wan_bandwidth()
        lan_devices = self._scrape_lan_bandwidth()

        if not wan_metrics and not lan_devices:
            return {}

        return {
            "run_id": run_id,
            "collected_at": collected_at,
            "wan": wan_metrics,
            "lan_devices": lan_devices,
        }

    def _scrape_wan_bandwidth(self) -> Dict[str, str]:
        if not self._select_bandwidth_tab("WAN"):
            return {}

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        block = find_first_usage_block(soup)
        if not block:
            LOGGER.debug("WAN tab did not contain a usage table.")
            return {}

        metrics = extract_one_hour_usage(block)
        if not metrics:
            LOGGER.debug("WAN usage table did not expose a 1hr column.")
            return {}

        upload_bytes = size_to_bytes(metrics.get("upload", ""))
        download_bytes = size_to_bytes(metrics.get("download", ""))

        LOGGER.info(
            "Captured WAN throughput (1hr): upload=%d bytes, download=%d bytes",
            upload_bytes,
            download_bytes,
        )
        return {
            "upload_1hr": upload_bytes,
            "download_1hr": download_bytes,
        }

    def _scrape_lan_bandwidth(self) -> List[Dict[str, str]]:
        if not self._select_bandwidth_tab("LAN"):
            return []

        self._expand_all_bandwidth_rows()
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        spans = soup.select("span.band-row-text")
        devices: List[Dict[str, str]] = []
        total = len(spans)
        for index, span in enumerate(spans, start=1):
            summary_row = span.find_parent("div", class_="row")
            if not summary_row:
                continue
            block_parent = summary_row.parent
            if not block_parent:
                continue
            detail_candidates = [child for child in block_parent.find_all(recursive=False) if child.name == "div"]
            if len(detail_candidates) < 2:
                continue
            usage_block = detail_candidates[1]
            usage_metrics = extract_one_hour_usage(usage_block)
            if not usage_metrics:
                continue

            cells = summary_row.select('div[role="cell"]')
            ip_address = clean_text(cells[2]) if len(cells) >= 3 else ""
            total_usage_str = clean_text(cells[3]) if len(cells) >= 4 else ""

            device_name = clean_text(span)
            LOGGER.info("Collecting bandwidth metrics for %s (%d/%d)", device_name or "Unknown", index, total)
            total_usage_bytes = size_to_bytes(total_usage_str)
            upload_bytes = size_to_bytes(usage_metrics.get("upload", ""))
            download_bytes = size_to_bytes(usage_metrics.get("download", ""))
            devices.append(
                {
                    "device_name": device_name,
                    "ip_address": ip_address,
                    "total_usage": total_usage_bytes,
                    "upload_1hr": upload_bytes,
                    "download_1hr": download_bytes,
                }
            )

        LOGGER.info("Captured bandwidth metrics for %d LAN devices.", len(devices))
        return devices

    def _select_bandwidth_tab(self, label: str) -> bool:
        xpath = f"//div[contains(@class,'cat-info') and normalize-space()='{label}']"
        try:
            tab = self.driver.find_element(By.XPATH, xpath)
        except NoSuchElementException:
            LOGGER.warning("Unable to find %s tab on the bandwidth page.", label)
            return False

        classes = tab.get_attribute("class") or ""
        if "cat_highlight" not in classes:
            tab.click()
        try:
            self.wait.until(
                lambda driver: "cat_highlight"
                in driver.find_element(By.XPATH, xpath).get_attribute("class")
            )
        except TimeoutException:
            LOGGER.warning("Timed out waiting for %s tab to activate.")
            return False

        time.sleep(self.delay)
        return True

    def _expand_all_bandwidth_rows(self) -> None:
        LOGGER.debug("Expanding LAN bandwidth rows to expose per-device throughput.")
        index = 0
        stagnation = 0
        max_scrolls = 80
        scroll_iterations = 0
        while True:
            spans = self.driver.find_elements(By.CSS_SELECTOR, "span.band-row-text")
            if not spans:
                LOGGER.warning("No device entries found on the LAN bandwidth tab.")
                return
            if index >= len(spans):
                self.driver.execute_script("window.scrollBy(0, window.innerHeight);")
                time.sleep(self.delay)
                stagnation += 1
                scroll_iterations += 1
                if stagnation >= 5 or scroll_iterations >= max_scrolls:
                    break
                continue

            stagnation = 0
            span = spans[index]
            index += 1
            self._expand_bandwidth_row(span)

    def _expand_bandwidth_row(self, span: Any) -> None:
        try:
            row = span.find_element(By.XPATH, "./ancestor::div[contains(@class,'row')][1]")
        except WebDriverException:
            return

        detail_rows = row.find_elements(By.XPATH, "following-sibling::div[1]/div[contains(@class,'row')]")
        if len(detail_rows) >= 3:
            return

        try:
            toggle = row.find_element(By.CSS_SELECTOR, "span.vs__open-indicator")
        except NoSuchElementException:
            return

        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", toggle)
        try:
            toggle.click()
        except WebDriverException:
            self.driver.execute_script("arguments[0].click();", toggle)
        time.sleep(self.delay)


def find_first_usage_block(soup: BeautifulSoup):
    for container in soup.select("div.scroll-content-box div"):
        rows = container.find_all("div", class_="row", recursive=False)
        if len(rows) < 3:
            continue
        first_cell = rows[0].find("div", recursive=False)
        if not first_cell:
            continue
        if normalize_label(first_cell.get_text()) != "usage":
            continue
        return container
    return None


def extract_one_hour_usage(block) -> Dict[str, str]:
    rows = block.find_all("div", class_="row", recursive=False)
    if len(rows) < 3:
        return {}

    header_cols = rows[0].find_all("div", recursive=False)
    header_lookup = {normalize_label(clean_text(col)): idx for idx, col in enumerate(header_cols)}
    one_hour_idx = header_lookup.get("1hr")
    if one_hour_idx is None:
        return {}

    results: Dict[str, str] = {}
    for row in rows[1:]:
        columns = row.find_all("div", recursive=False)
        if not columns:
            continue
        label = normalize_label(clean_text(columns[0]))
        if label not in {"upload", "download"}:
            continue
        if one_hour_idx >= len(columns):
            continue
        results[label] = clean_text(columns[one_hour_idx])
    return results


def size_to_bytes(raw_value: Optional[str]) -> int:
    if not raw_value:
        return 0
    text = str(raw_value).strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    match = SIZE_PATTERN.match(text)
    if not match:
        return 0
    value = float(match.group("value"))
    unit = (match.group("unit") or "bytes").lower()
    multiplier = {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kb": 1024,
        "kilobyte": 1024,
        "kilobytes": 1024,
        "mb": 1024 ** 2,
        "megabyte": 1024 ** 2,
        "megabytes": 1024 ** 2,
        "gb": 1024 ** 3,
        "gigabyte": 1024 ** 3,
        "gigabytes": 1024 ** 3,
        "tb": 1024 ** 4,
        "terabyte": 1024 ** 4,
        "terabytes": 1024 ** 4,
    }.get(unit, 1)
    return int(value * multiplier)


def write_bandwidth_output(payload: Dict[str, object], output_dir: Path) -> Optional[Path]:
    if not payload:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"device_bandwidth_{timestamp}.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    lan_count = len(payload.get("lan_devices", [])) if isinstance(payload.get("lan_devices"), list) else 0
    LOGGER.info("Wrote bandwidth metrics for %d devices to %s", lan_count, output_path)
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

    scraper = BandwidthScraper(
        base_url=config.get("url", "https://192.168.1.1"),
        password=password,
        headless=headless_cfg,
        output_dir=output_dir,
        driver_path=driver_path,
        delay=args.delay,
    )

    try:
        payload = scraper.collect()
        write_bandwidth_output(payload, output_dir)
    except KeyboardInterrupt:
        LOGGER.warning("Scraper interrupted by user.")
        return 1
    except Exception:
        LOGGER.exception("Bandwidth scraper failed.")
        return 1
    finally:
        scraper.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
