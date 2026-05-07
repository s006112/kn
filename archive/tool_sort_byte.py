import re

LOG_FILE = "vpn_log_nov26.txt"     # 換成你的檔名
OUTPUT   = "bytes_stats.txt" # 統計輸出檔案

pattern = re.compile(r"bytes=(\d+)")

records = []

with open(LOG_FILE, "r", errors="ignore") as f:
    for line in f:
        m = pattern.search(line)
        if m:
            bytes_val = int(m.group(1))
            records.append((bytes_val, line.strip()))

# 依 bytes 值排序（大到小）
records.sort(key=lambda x: x[0], reverse=True)

# 輸出結果
with open(OUTPUT, "w") as out:
    total = 0
    for b, line in records:
        total += b
        out.write(f"{b:15d}  {line}\n")

    out.write("\n")
    out.write(f"TOTAL BYTES = {total} bytes\n")
    out.write(f"TOTAL GB    = {total / (1024**3):.3f} GB\n")

print("Done. Results saved to", OUTPUT)
