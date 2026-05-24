import json
# 1. 記得在檔案最上方引入 datetime 模組
from datetime import timedelta
import yt_dlp

target_channel = "https://www.youtube.com/@nowhereman_44/videos"

def get_channel_videos(channel_url):
    ydl_opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
    }

    video_list = []
    print(f"正在解析頻道: {channel_url}，請稍候...")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            result = ydl.extract_info(channel_url, download=False)
            if "entries" in result:
                for entry in result["entries"]:
                    if entry:
                        # --- 核心修改部分開始 ---
                        # 先取得原始秒數（可能為 None，例如直播尚未結束或預告片）
                        seconds = entry.get("duration")

                        if seconds is not None:
                            # 使用 timedelta 將秒數轉成 時:分:秒 格式
                            formatted_duration = str(
                                timedelta(seconds=int(seconds))
                            )
                        else:
                            formatted_duration = "未知"
                        # --- 核心修改部分結束 ---

                        video_data = {
                            "title": entry.get("title"),
                            "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                            "duration": formatted_duration,  # 這裡就變成了 "HH:MM:SS" 格式
                        }
                        video_list.append(video_data)

            print(f"解析完成！共找到 {len(video_list)} 部影片。")
            return video_list
        except Exception as e:
            print(f"解析發生錯誤: {e}")
            return []

if __name__ == "__main__":
    videos = get_channel_videos(target_channel)

    # 儲存為 JSON 檔案
    if videos:
        with open(
            "channel_videos.json", "w", encoding="utf-8"
        ) as f:
            json.dump(videos, f, ensure_ascii=False, indent=4)
        print("資料已成功儲存至 channel_videos.json")