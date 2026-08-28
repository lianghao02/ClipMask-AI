"""
ClipMask-AI Subtitle Generator & SRT Manager
支援繁體中文 SRT 字幕產生、解析與時間校正。
"""
import os
import subprocess
from typing import List, Dict, Any, Optional

class SubtitleManager:
    @staticmethod
    def format_srt_timestamp(seconds: float) -> str:
        """將秒數轉為 SRT 標準時間戳格式 HH:MM:SS,mmm"""
        ms = int(round((seconds - int(seconds)) * 1000))
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def generate_srt_file(subtitles: List[Dict[str, Any]], output_srt_path: str):
        """
        將字幕清單 [{'start': 1.0, 'end': 3.5, 'text': '你好'}] 輸出為標準 SRT 檔案
        """
        with open(output_srt_path, "w", encoding="utf-8") as f:
            for idx, sub in enumerate(subtitles, 1):
                start_str = SubtitleManager.format_srt_timestamp(sub["start"])
                end_str = SubtitleManager.format_srt_timestamp(sub["end"])
                text = sub["text"].strip()
                f.write(f"{idx}\n{start_str} --> {end_str}\n{text}\n\n")

    @staticmethod
    def parse_srt_file(srt_path: str) -> List[Dict[str, Any]]:
        """解析 SRT 檔案為字幕字典列表"""
        if not os.path.exists(srt_path):
            return []
            
        subtitles = []
        with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip().replace("\r\n", "\n")
            
        blocks = content.split("\n\n")
        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) >= 3:
                time_line = lines[1]
                if "-->" in time_line:
                    start_s, end_s = time_line.split("-->")
                    start_t = SubtitleManager._parse_srt_time(start_s.strip())
                    end_t = SubtitleManager._parse_srt_time(end_s.strip())
                    text = "\n".join(lines[2:])
                    subtitles.append({"start": start_t, "end": end_t, "text": text})
        return subtitles

    @staticmethod
    def _parse_srt_time(time_str: str) -> float:
        """HH:MM:SS,mmm 轉秒數"""
        time_str = time_str.replace(",", ".")
        parts = time_str.split(":")
        h = float(parts[0])
        m = float(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
