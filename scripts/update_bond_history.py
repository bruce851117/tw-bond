#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TPEx 台債歷史資料更新器。

正式檔名固定為 update_bond_history.py。
- bootstrap: 回補 2026-01-01 至指定日期。
- daily: 僅抓指定日期，若未指定則抓台北日期。
- 已有日期預設跳過；--force 可覆寫。
- 新券號不需改 schema，會自然加入當日 records。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import xlrd

BASE_URL = "https://www.tpex.org.tw/storage/bond_zone/tradeinfo/govbond"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEBUG_DIR = ROOT / "debug"
TAIPEI = ZoneInfo("Asia/Taipei")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9]{4,9}$", re.I)

REPORTS = {
    "BDdys01a": {
        "output": DATA_DIR / "ebts_outright.json",
        "description": "等殖成交行情表（買賣斷）",
    },
    "BDdcs001": {
        "output": DATA_DIR / "otc_outright.json",
        "description": "營業處所成交行情表（買賣斷）",
    },
}


def setup_logging() -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(DEBUG_DIR / "update.log", encoding="utf-8")],
    )


def url_for(report: str, day: date) -> str:
    ymd = day.strftime("%Y%m%d")
    return f"{BASE_URL}/{day:%Y}/{day:%Y%m}/{report}.{ymd}-C.xls"


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "application/vnd.ms-excel,application/octet-stream;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        "Referer": "https://www.tpex.org.tw/zh-tw/bond/tradeinfo/govbond/govbonddaily.html",
    })
    try:
        s.get("https://www.tpex.org.tw/zh-tw/bond/tradeinfo/govbond/govbonddaily.html", timeout=30)
    except requests.RequestException:
        pass
    return s


def download(session: requests.Session, report: str, day: date) -> bytes | None:
    url = url_for(report, day)
    for attempt in range(1, 4):
        try:
            r = session.get(url, timeout=45)
            logging.info("GET %s -> %s bytes=%s attempt=%s", url, r.status_code, len(r.content), attempt)
            if r.status_code in (403, 429, 500, 502, 503, 504):
                time.sleep((2 ** attempt) + random.random())
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            if len(r.content) < 512 or r.content[:4] not in (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"):
                logging.warning("非預期 Excel 內容: %s content-type=%s", url, r.headers.get("content-type"))
                return None
            return r.content
        except requests.RequestException as exc:
            logging.warning("下載失敗 %s: %s", url, exc)
            time.sleep((2 ** attempt) + random.random())
    return None


def clean_value(v: Any) -> Any:
    if isinstance(v, float):
        if v.is_integer():
            return int(v)
        return v
    if v is None:
        return None
    s = str(v).replace("\u3000", " ").strip()
    return s if s else None


def unique_headers(values: list[Any]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for i, value in enumerate(values, 1):
        base = str(clean_value(value) or f"column_{i}").replace("\n", " ").strip()
        seen[base] = seen.get(base, 0) + 1
        result.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return result


def parse_xls(content: bytes, report: str, day: date, source_url: str) -> dict[str, Any]:
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    matrix = [[clean_value(sheet.cell_value(r, c)) for c in range(sheet.ncols)] for r in range(sheet.nrows)]

    header_row = None
    for idx, row in enumerate(matrix[:30]):
        joined = " ".join(str(x or "") for x in row)
        if "代號" in joined or "Code" in joined or "債券代碼" in joined:
            header_row = idx
    if header_row is None:
        raise ValueError(f"找不到表頭: {report} {day}")

    headers = unique_headers(matrix[header_row])
    records: list[dict[str, Any]] = []
    for row in matrix[header_row + 1:]:
        values = row[:len(headers)] + [None] * max(0, len(headers) - len(row))
        first_cells = [str(v).strip() for v in values[:4] if v not in (None, "")]
        if not first_cells:
            continue
        if any(x.lower() in ("合計", "total", "註", "remark") for x in first_cells):
            break
        code = next((x.upper() for x in first_cells if CODE_RE.fullmatch(x)), None)
        if not code:
            continue
        item = {h: v for h, v in zip(headers, values) if v is not None}
        item["bond_code"] = code
        records.append(item)

    return {
        "date": day.isoformat(),
        "report": report,
        "source_url": source_url,
        "sha256": hashlib.sha256(content).hexdigest(),
        "sheet_name": sheet.name,
        "header_row_1_based": header_row + 1,
        "columns": headers,
        "record_count": len(records),
        "records": records,
    }


def load_store(path: Path, report: str, description: str) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"metadata": {"dataset": report, "description": description, "last_updated": None}, "dates": {}}


def save_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    store["metadata"]["last_updated"] = datetime.now(TAIPEI).isoformat(timespec="seconds")
    store["dates"] = dict(sorted(store["dates"].items()))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("bootstrap", "daily"), default="daily")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end")
    parser.add_argument("--date")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.35)
    args = parser.parse_args()
    setup_logging()

    today = datetime.now(TAIPEI).date()
    if args.mode == "daily":
        target = date.fromisoformat(args.date) if args.date else today
        days = [target]
    else:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end) if args.end else today
        days = list(daterange(start, end))

    session = new_session()
    changed = 0
    for report, cfg in REPORTS.items():
        store = load_store(cfg["output"], report, cfg["description"])
        for day in days:
            key = day.isoformat()
            if key in store["dates"] and not args.force:
                continue
            content = download(session, report, day)
            if content is None:
                continue
            try:
                parsed = parse_xls(content, report, day, url_for(report, day))
                store["dates"][key] = parsed
                save_store(cfg["output"], store)
                changed += 1
                logging.info("寫入 %s %s records=%s", report, key, parsed["record_count"])
            except Exception:
                DEBUG_DIR.mkdir(exist_ok=True)
                bad = DEBUG_DIR / f"{report}.{day:%Y%m%d}.xls"
                bad.write_bytes(content)
                logging.exception("解析失敗，原檔已保存: %s", bad)
            time.sleep(args.sleep)
    logging.info("完成，新增或覆寫 %s 個報表日期", changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
