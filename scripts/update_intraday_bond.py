#!/usr/bin/env python3
"""更新 TPEx 公債盤中報價、公債盤中成交與公司債盤中成交。"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "intraday"
DEBUG_DIR = ROOT / "debug" / "intraday"
QUOTE_HISTORY = OUT_DIR / "government_quote_history.json"
DASHBOARD_FILE = OUT_DIR / "intraday_market.json"

SOURCES = {
    "government_quote": {
        "url": "https://www.tpex.org.tw/www/zh-tw/bond/opsQuotes",
        "referer": "https://www.tpex.org.tw/zh-tw/bond/info/market/ebts-ops/quote.html",
        "title": "公債盤中報價行情",
    },
    "government_trade": {
        "url": "https://www.tpex.org.tw/www/zh-tw/bond/opsTrade",
        "referer": "https://www.tpex.org.tw/zh-tw/bond/info/market/ebts-ops/trade.html",
        "title": "公債盤中成交行情",
    },
    "company_trade": {
        "url": "https://www.tpex.org.tw/www/zh-tw/bond/cbiTrade",
        "referer": "https://www.tpex.org.tw/zh-tw/bond/info/market/otc-cb/trade.html",
        "title": "公司債盤中成交行情",
    },
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("無法讀取 %s，改用預設值", path)
        return fallback


def clean_key(value: Any) -> str:
    return re.sub(r"[\s　_()（）/%％\\-]+", "", str(value or "")).lower()


def as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "").replace("%", "").replace("％", "")
    if not text or text in {"-", "--", "－", "N/A", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def field_label(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("title", "name", "label", "field", "key"):
            if value.get(key) not in (None, ""):
                return str(value[key])
    return str(value)


def find_tables(node: Any, path: str = "root") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    if isinstance(node, dict):
        fields = node.get("fields") or node.get("columns") or node.get("headers")
        rows = node.get("data") or node.get("rows")
        if isinstance(fields, list) and isinstance(rows, list):
            labels = [field_label(item) for item in fields]
            normalized = []
            for row in rows:
                if isinstance(row, dict):
                    normalized.append(row)
                elif isinstance(row, list):
                    normalized.append({labels[i] if i < len(labels) else f"col_{i+1}": v for i, v in enumerate(row)})
            tables.append({"path": path, "fields": labels, "rows": normalized})
        for key, value in node.items():
            tables.extend(find_tables(value, f"{path}.{key}"))
    elif isinstance(node, list):
        if node and all(isinstance(item, dict) for item in node):
            keys: list[str] = []
            for item in node:
                for key in item:
                    if key not in keys:
                        keys.append(key)
            tables.append({"path": path, "fields": keys, "rows": node})
        for index, value in enumerate(node):
            if isinstance(value, (dict, list)):
                tables.extend(find_tables(value, f"{path}[{index}]"))
    return tables


def best_table(payload: Any) -> dict[str, Any]:
    tables = find_tables(payload)
    if not tables:
        return {"path": None, "fields": [], "rows": []}
    tables.sort(key=lambda item: (len(item["rows"]), len(item["fields"])), reverse=True)
    return tables[0]


def value_by_alias(row: dict[str, Any], aliases: Iterable[str], exclude: Iterable[str] = ()) -> Any:
    normalized = [(key, clean_key(key), value) for key, value in row.items()]
    excluded = [clean_key(item) for item in exclude]
    for alias in aliases:
        target = clean_key(alias)
        for _, key, value in normalized:
            if target == key and not any(word in key for word in excluded):
                return value
    for alias in aliases:
        target = clean_key(alias)
        for _, key, value in normalized:
            if target in key and not any(word in key for word in excluded):
                return value
    return None


def bond_code(row: dict[str, Any]) -> str:
    value = value_by_alias(row, ["債券代號", "債券代碼", "代號 Code", "bond_code", "code"])
    return str(value or "").strip().upper()


def bond_name(row: dict[str, Any]) -> str:
    value = value_by_alias(row, ["債券名稱", "名稱 Name", "bond_name", "name"])
    return str(value or "").strip()


def date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def maturity_date(row: dict[str, Any]) -> str | None:
    return date_text(value_by_alias(row, ["到期日期", "到期日", "maturity_date", "maturitydate"]))


def remaining_years(maturity: str | None, as_of: date) -> float | None:
    if not maturity:
        return None
    try:
        maturity_day = date.fromisoformat(maturity)
    except ValueError:
        return None
    return round((maturity_day - as_of).days / 365.25, 1)


def normalize_quote(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        code = bond_code(row)
        if not code or code.startswith("F"):
            continue
        bid = as_number(value_by_alias(row, ["買進殖利率/百元價", "買進殖利率", "買進", "bid"]))
        ask = as_number(value_by_alias(row, ["賣出殖利率/百元價", "賣出殖利率", "賣出", "ask"]))
        output.append({"bond_code": code, "bond_name": bond_name(row), "bid": bid, "ask": ask, "raw": row})
    return output


def normalize_trade(rows: list[dict[str, Any]], as_of: date, company: bool) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        code = bond_code(row)
        if not code or code.startswith("F"):
            continue
        last = as_number(value_by_alias(row, ["最近殖利率/百元價", "最近殖利率", "最新殖利率", "成交殖利率", "lastyield", "last"]))
        maturity = maturity_date(row)
        output.append({
            "bond_code": code,
            "bond_name": bond_name(row),
            "issuer_name": "" if not company else issuer_from_master(code),
            "maturity_date": maturity,
            "remaining_years": remaining_years(maturity, as_of),
            "last_rate": last,
            "high_rate": as_number(value_by_alias(row, ["最高殖利率/百元價", "最高殖利率", "highyield"], exclude=["價格"])),
            "low_rate": as_number(value_by_alias(row, ["最低殖利率/百元價", "最低殖利率", "lowyield"], exclude=["價格"])),
            "raw": row,
        })
    return output


_MASTER: dict[str, dict[str, Any]] = {}


def load_master() -> None:
    global _MASTER
    candidates = [ROOT / "data" / "bond_master.json", ROOT / "data" / "manual_bond_mapping.json"]
    merged: dict[str, dict[str, Any]] = {}
    for path in candidates:
        value = read_json(path, {})
        bonds = value.get("bonds", value) if isinstance(value, dict) else {}
        if isinstance(bonds, dict):
            for code, record in bonds.items():
                if isinstance(record, dict):
                    merged[str(code).upper()] = {**merged.get(str(code).upper(), {}), **record}
        elif isinstance(bonds, list):
            for record in bonds:
                if isinstance(record, dict) and record.get("bond_code"):
                    merged[str(record["bond_code"]).upper()] = {**merged.get(str(record["bond_code"]).upper(), {}), **record}
    _MASTER = merged


def issuer_from_master(code: str) -> str:
    record = _MASTER.get(code) or _MASTER.get(re.sub(r"R$", "", code)) or {}
    return str(record.get("issuer_name") or "").strip()


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.tpex.org.tw",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Cache-Control": "no-cache",
    })
    cookie = os.getenv("TPEX_COOKIE", "").strip()
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def fetch(session: requests.Session, name: str, config: dict[str, str]) -> tuple[Any, dict[str, Any]]:
    last_error: Exception | None = None
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 4):
        try:
            response = session.post(config["url"], headers={"Referer": config["referer"]}, data={"id": "", "response": "json"}, timeout=45)
            (DEBUG_DIR / f"{name}_response.txt").write_text(response.text, encoding="utf-8", errors="replace")
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:240]}")
            payload = response.json()
            table = best_table(payload)
            if not table["rows"]:
                raise RuntimeError(f"JSON成功，但找不到資料列；table_path={table['path']}")
            return payload, table
        except Exception as exc:
            last_error = exc
            logging.warning("%s attempt %s failed: %s", name, attempt, exc)
            time.sleep(attempt * 5)
    raise RuntimeError(f"{name} 抓取失敗：{last_error}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_master()
    now = datetime.now(TAIPEI)
    today = now.date().isoformat()
    fetched_at = now.isoformat(timespec="seconds")
    session = make_session()
    status: dict[str, Any] = {}
    normalized: dict[str, list[dict[str, Any]]] = {}

    for name, config in SOURCES.items():
        try:
            raw, table = fetch(session, name, config)
            write_json(DEBUG_DIR / f"{name}_raw.json", raw)
            if name == "government_quote":
                rows = normalize_quote(table["rows"])
            else:
                rows = normalize_trade(table["rows"], now.date(), company=name == "company_trade")
            normalized[name] = rows
            status[name] = {"ok": True, "rows": len(rows), "table_path": table["path"]}
        except Exception as exc:
            logging.exception("%s failed", name)
            status[name] = {"ok": False, "error": str(exc)}

    if not normalized:
        write_json(DEBUG_DIR / "summary.json", {"updated_at": fetched_at, "sources": status})
        raise RuntimeError("三個盤中來源全部失敗，請下載 intraday-debug artifact")

    history = read_json(QUOTE_HISTORY, {"metadata": {"description": "公債每日盤中報價歷史"}, "dates": {}})
    history.setdefault("dates", {})
    if "government_quote" in normalized:
        history["dates"][today] = {"updated_at": fetched_at, "rows": normalized["government_quote"]}
    history["metadata"]["updated_at"] = fetched_at
    write_json(QUOTE_HISTORY, history)

    if "government_trade" in normalized:
        write_json(OUT_DIR / "government_trade_latest.json", {"metadata": {"updated_at": fetched_at, "trade_date": today}, "rows": normalized["government_trade"]})
    if "company_trade" in normalized:
        write_json(OUT_DIR / "company_trade_latest.json", {"metadata": {"updated_at": fetched_at, "trade_date": today}, "rows": normalized["company_trade"]})

    dashboard = {
        "metadata": {"updated_at": fetched_at, "trade_date": today, "sources": status},
        "government_quote": {"date": today, "rows": normalized.get("government_quote", history.get("dates", {}).get(today, {}).get("rows", []))},
        "government_trade": {"date": today, "rows": normalized.get("government_trade", [])},
        "company_trade": {"date": today, "rows": normalized.get("company_trade", [])},
    }
    write_json(DASHBOARD_FILE, dashboard)
    write_json(DEBUG_DIR / "summary.json", dashboard["metadata"])
    print(json.dumps(dashboard["metadata"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
