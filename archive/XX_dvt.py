#!/usr/bin/env python3
"""
05_dvt.py

掃描 raw_pdf_dir 中的 PDF 檔案，使用 chunk_pdf 提取完整文本，
並將每份 PDF 另存為 .txt 文件到 processed_pdf 目錄。
"""

import logging
from pathlib import Path
from chunk_pdf_a import get_pdf_full_text

# 路徑設定
raw_pdf_dir: Path = Path("/root/email-rag/data/raw/pdf/")
processed_pdf: Path = Path("/root/email-rag/data/clean/pdf/")

# 日誌設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

def main():
    # 確保輸出資料夾存在，若不存在則自動建立（包括中間資料夾）
    processed_pdf.mkdir(parents=True, exist_ok=True)

    # 遍歷 raw_pdf_dir 中所有 .pdf 檔案
    for pdf_path in raw_pdf_dir.glob("*.pdf"):
        logger.info(f"處理檔案：{pdf_path.name}")
        try:
            # 讀取 PDF 檔案的二進位內容（bytes）
            data = pdf_path.read_bytes()

            # 調用共用函數提取整份 PDF 的純文字（已清洗、已合併頁面）
            full_text = get_pdf_full_text(data, filename=pdf_path.name)

            # 檢查是否提取到有效文本，若為空則略過
            if not full_text.strip():
                logger.warning(f"{pdf_path.name} 無有效文本，略過")
                continue

            # 定義輸出檔案路徑（與原始 PDF 同名但副檔名改為 .txt）
            output_path = processed_pdf / f"{pdf_path.stem}.txt"

            # 將提取結果寫入 .txt 文件，使用 UTF-8 編碼
            output_path.write_text(full_text, encoding="utf-8")

            logger.info(f"匯出完成：{output_path}")

        except Exception as e:
            # 若處理過程中發生錯誤，記錄錯誤訊息與完整 traceback
            logger.error(f"處理失敗 {pdf_path.name}: {e}", exc_info=True)

if __name__ == "__main__":
    main()
