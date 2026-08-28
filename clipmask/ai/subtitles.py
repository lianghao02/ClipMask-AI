"""
ClipMask-AI 聽打字幕模型與多文字繪製模組
支援自訂字型、半透明字卡底色與 OpenCV 影像繁中燒錄。
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple
import os
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

@dataclass
class SubtitleItem:
    id: int
    start_sec: float
    end_sec: float
    text: str

    def __getitem__(self, item):
        if item in ("start", "start_sec"):
            return self.start_sec
        elif item in ("end", "end_sec"):
            return self.end_sec
        elif item == "text":
            return self.text
        elif item == "id":
            return self.id
        raise KeyError(item)

class SubtitleManager:
    @staticmethod
    def parse_srt_file(file_path: str) -> List[SubtitleItem]:
        if not os.path.exists(file_path):
            return []
        items = []
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="cp950", errors="ignore") as f:
                content = f.read()

        blocks = content.strip().split("\n\n")
        idx = 1
        for block in blocks:
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if len(lines) >= 2:
                time_line = lines[1] if lines[0].isdigit() else lines[0]
                text_lines = lines[2:] if lines[0].isdigit() else lines[1:]
                
                if "-->" in time_line:
                    parts = time_line.split("-->")
                    start_s = SubtitleManager._time_str_to_sec(parts[0].strip())
                    end_s = SubtitleManager._time_str_to_sec(parts[1].strip())
                    text = " ".join(text_lines)
                    items.append(SubtitleItem(id=idx, start_sec=start_s, end_sec=end_s, text=text))
                    idx += 1
        return items

    @staticmethod
    def generate_srt_file(items, output_path: str) -> bool:
        """相容 dict 或 SubtitleItem 格式產生 SRT 檔案"""
        sub_items = []
        for i, item in enumerate(items, start=1):
            if isinstance(item, dict):
                sub_items.append(SubtitleItem(id=i, start_sec=item["start"], end_sec=item["end"], text=item["text"]))
            else:
                sub_items.append(item)
        return SubtitleManager.export_srt_file(sub_items, output_path)

    @staticmethod
    def export_srt_file(items: List[SubtitleItem], output_path: str) -> bool:
        try:
            with open(output_path, "w", encoding="utf-8-sig") as f:
                for i, item in enumerate(items, start=1):
                    s_str = SubtitleManager._sec_to_srt_time(item.start_sec if isinstance(item, SubtitleItem) else item["start"])
                    e_str = SubtitleManager._sec_to_srt_time(item.end_sec if isinstance(item, SubtitleItem) else item["end"])
                    t_str = item.text if isinstance(item, SubtitleItem) else item["text"]
                    f.write(f"{i}\n{s_str} --> {e_str}\n{t_str}\n\n")
            return True
        except Exception:
            return False

    @staticmethod
    def get_active_subtitle_at(items: List[SubtitleItem], current_time: float) -> Optional[str]:
        for item in items:
            if item.start_sec <= current_time <= item.end_sec:
                return item.text
        return None

    @staticmethod
    def draw_subtitle_on_image(image_rgb: np.ndarray, text: str) -> np.ndarray:
        """在影像下方繪製高清晰度字卡（黑底半透明圓角 + 白色繁中文字）"""
        if not text:
            return image_rgb
            
        h, w = image_rgb.shape[:2]
        pil_img = Image.fromarray(image_rgb)
        draw = ImageDraw.Draw(pil_img, "RGBA")
        
        # 尋找 Windows 系統字型 (微軟正黑體)
        font_path = "C:\\Windows\\Fonts\\msjh.ttc"
        if not os.path.exists(font_path):
            font_path = "C:\\Windows\\Fonts\\msjh.ttf"
        if not os.path.exists(font_path):
            font_path = "C:\\Windows\\Fonts\\simhei.ttf"
            
        font_size = max(18, int(h * 0.042))
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        # 計算文字長寬與置中位置
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        pad_x = 16
        pad_y = 8
        box_w = text_w + pad_x * 2
        box_h = text_h + pad_y * 2
        
        x0 = (w - box_w) // 2
        y0 = int(h * 0.88) - box_h // 2
        x1 = x0 + box_w
        y1 = y0 + box_h

        # 繪製半透明黑色字卡底
        draw.rounded_rectangle([x0, y0, x1, y1], radius=8, fill=(18, 20, 24, 195))
        # 繪製白色文字
        draw.text((x0 + pad_x, y0 + pad_y - 2), text, font=font, fill=(255, 255, 255, 255))

        return np.array(pil_img.convert("RGB"))

    @staticmethod
    def draw_speech_indicator(image_rgb: np.ndarray) -> np.ndarray:
        """當偵測到人聲但尚未聽打時，在畫面右下角顯示柔和的語音偵測徽章"""
        h, w = image_rgb.shape[:2]
        pil_img = Image.fromarray(image_rgb).convert("RGBA")
        draw = ImageDraw.Draw(pil_img)

        badge_text = "🎙️ 偵測到人聲 (按 T 聽打)"
        font_size = max(13, int(h * 0.025))
        try:
            font_path = "C:\\Windows\\Fonts\\msjh.ttc"
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), badge_text, font=font)
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]

        pad_x = 12
        pad_y = 6
        box_w = bw + pad_x * 2
        box_h = bh + pad_y * 2

        x1 = w - 16
        x0 = x1 - box_w
        y1 = h - 20
        y0 = y1 - box_h

        # 繪製莫蘭迪鼠尾草綠半透明底
        draw.rounded_rectangle([x0, y0, x1, y1], radius=6, fill=(95, 135, 104, 210))
        draw.text((x0 + pad_x, y0 + pad_y - 1), badge_text, font=font, fill=(255, 255, 255, 255))

        return np.array(pil_img.convert("RGB"))

    @staticmethod
    def _time_str_to_sec(t_str: str) -> float:
        try:
            parts = t_str.replace(",", ".").split(":")
            h = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            return h * 3600.0 + m * 60.0 + s
        except Exception:
            return 0.0

    @staticmethod
    def _sec_to_srt_time(seconds: float) -> str:
        ms = int((seconds - int(seconds)) * 1000)
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
