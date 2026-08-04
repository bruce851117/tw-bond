# Taiwan Bond Dashboard Data Pipeline

本 Repo 每日下載櫃買中心兩份買賣斷成交日報表，更新兩份原始歷史 JSON，再抓取 TPEx 各類債券發行資料 API，建立債券主檔，最後把 EBTS 與 OTC 合併成前端可直接使用的 JSON。

## 產出檔案

### 原始成交歷史

- `data/ebts_outright.json`：等殖成交系統 `BDdys01a`
- `data/otc_outright.json`：營業處所議價 `BDdcs001`

### API 債券主檔

- `data/bond_master.json`

此檔保存 TPEx `bond_ISSBD1_data` 至 `bond_ISSBD11_data` 的資料。每檔債券包含標準化欄位，也保留 API 原始資料於 `raw`，後續可再 Mapping 發行額、流通餘額、幣別、票息及其他欄位。

### 合併成交資料

- `data/merged_outright.json`

合併檔每筆資料會加入：

- `market`：`EBTS` 或 `OTC`
- `market_zh`：`等殖` 或 `處所`
- `bond_code`
- `base_bond_code`
- `issuer_name`
- `bond_type`
- `issue_date`
- `maturity_date`
- `coupon_rate`
- `is_perpetual`
- `latest_remaining_years`
- `remaining_years_as_of`
- `master_mapped`

最新剩餘年期計算方式：

```text
round((到期日 - 台北執行日期).days / 365.25, 1)
```

沒有到期日的永續債保留 `latest_remaining_years: null`，並標記 `is_perpetual: true`。

## TPEx 發行資料 API

程式每日抓取：

- `bond_ISSBD1_data`：政府公債
- `bond_ISSBD2_data`：外國金融債
- `bond_ISSBD3_data`：金融債券
- `bond_ISSBD4_data`：普通公司債
- `bond_ISSBD5_data`：轉換或交換公司債
- `bond_ISSBD6_data`：海外轉換公司債
- `bond_ISSBD7_data`：附認股權公司債
- `bond_ISSBD8_data`：海外附認股權公司債
- `bond_ISSBD9_data`：海外普通債
- `bond_ISSBD10_data`：國際債券
- `bond_ISSBD11_data`：國際債券外國發行人

若某一API暫時失敗，程式會保留既有 `bond_master.json` 中該來源的舊資料，不會因其他API成功而把該類債券刪掉。API執行狀態會記錄在 `bond_master.json > metadata > source_status`。

## 第一次回補 2026 年資料

到 GitHub：

```text
Actions > Update Taiwan bond history > Run workflow
```

設定：

```text
mode: bootstrap
start: 2026-01-01
end: 2026-08-03
force: false
```

## 每日更新

排程於週一至週五台灣時間 17:30 執行 `daily`：

1. 更新兩份原始成交 JSON。
2. 重新抓取各類債券發行 API。
3. 更新 `bond_master.json`。
4. 重新產生完整 `merged_outright.json`。
5. 驗證四份 JSON。
6. 上傳 Debug Artifact。
7. Commit 並 Push 回 Repo。

## 只重建 Mapping 與合併檔

如果原始 EBTS／OTC JSON 已存在，只想重新抓主檔 API 並重建合併檔：

```bash
python scripts/update_bond_history.py --mode rebuild
```

## Debug

- 所有 HTTP URL、狀態碼、下載大小與重試次數寫入 `debug/update.log`。
- XLS 解析失敗時保存原始檔於 `debug/`。
- 合併檔 metadata 會列出沒有 Mapping 到主檔的券號。
- GitHub Actions 每次執行都上傳 Debug Artifact，保留 30 天。
