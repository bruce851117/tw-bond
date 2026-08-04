# Taiwan Bond Dashboard Data Pipeline

這個 starter repo 先完成兩份櫃買中心日報表的歷史回補與每日增量更新：

- `BDdys01a`：等殖成交行情表（買賣斷）
- `BDdcs001`：營業處所成交行情表（買賣斷）

## 資料輸出

- `data/ebts_outright.json`
- `data/otc_outright.json`

JSON 以日期為 key，每天保留來源網址、SHA-256、原欄位、筆數及全部券號紀錄。因此新檔案出現舊檔案沒有的券號時，會直接加入該日 `records`，不需要先建立固定券號欄位。

## 第一次回補 2026 年以來資料

到 GitHub 的 **Actions > Update Taiwan bond history > Run workflow**：

- mode：`bootstrap`
- start：`2026-01-01`
- end：可留白，或填 `2026-08-03`
- force：`false`

程式會嘗試每個曆日。非交易日或尚未發布的檔案通常為 404，會跳過；已存在於 JSON 的日期也會跳過。

## 每日自動更新

工作流程在週一至週五台灣時間 17:30 執行。櫃買檔案若尚未上線，當天不會寫入；可稍後手動執行 `daily`。

## 本機執行

```bash
python -m pip install -r requirements.txt
python scripts/update_bond_history.py --mode bootstrap --start 2026-01-01 --end 2026-08-03
python scripts/update_bond_history.py --mode daily --date 2026-08-04
```

## Debug 與稽核

- 每次 HTTP 狀態、URL、下載大小與重試次數會寫入 `debug/update.log`。
- 解析失敗時，原始 XLS 會存入 `debug/`。
- GitHub Actions 每次執行都會上傳 debug artifact，保留30天。
- 每筆日期資料保留來源 URL 與檔案 SHA-256，方便事後核對。

## 注意

若櫃買中心對 GitHub Hosted Runner 回傳 403，建議改用 self-hosted runner，或再加入官方頁面實際呼叫的下載 API。程式已先造訪櫃買頁面取得 Cookie，並使用瀏覽器 User-Agent、Referer 與退避重試。
