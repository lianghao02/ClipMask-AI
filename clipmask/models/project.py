"""ClipMask-AI 專案核心資料模型"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
import json

@dataclass
class Keyframe:
    time: float  # 秒數基準
    pts: Optional[int] = None
    rect_px: Tuple[int, int, int, int] = (0, 0, 0, 0)  # [x, y, w, h] 原生像素座標
    source: str = "manual"  # manual | tracker | face_detector | plate_detector

@dataclass
class MaskConfig:
    style: str = "mosaic"  # mosaic | blur
    strength: int = 15  # 馬賽克塊大小或模糊半徑
    padding: float = 0.15  # 四周各外擴 15%
    privacy_mode: bool = True  # Hard edge

@dataclass
class Track:
    id: str
    label: str = "遮蔽物件"
    type: str = "manual"  # face | plate | manual
    enabled: bool = True
    mask: MaskConfig = field(default_factory=MaskConfig)
    keyframes: List[Keyframe] = field(default_factory=list)

    def add_or_update_keyframe(self, time: float, rect_px: Tuple[int, int, int, int], pts: Optional[int] = None, source: str = "manual"):
        for kf in self.keyframes:
            if abs(kf.time - time) < 1e-4:
                kf.rect_px = rect_px
                kf.pts = pts
                kf.source = source
                return
        self.keyframes.append(Keyframe(time=time, pts=pts, rect_px=rect_px, source=source))
        self.keyframes.sort(key=lambda k: k.time)

    def remove_keyframe_at(self, time: float, tolerance: float = 0.05) -> bool:
        before_len = len(self.keyframes)
        self.keyframes = [k for k in self.keyframes if abs(k.time - time) > tolerance]
        return len(self.keyframes) < before_len

@dataclass
class WorkRange:
    in_time: float = 0.0
    out_time: float = 0.0
    in_pts: Optional[int] = None
    out_pts: Optional[int] = None

@dataclass
class SourceMetadata:
    path: str
    width: int
    height: int
    fps: float
    duration: float
    time_base: str = "1/90000"

@dataclass
class ProjectState:
    version: int = 1
    source: Optional[SourceMetadata] = None
    work_range: Optional[WorkRange] = None
    tracks: List[Track] = field(default_factory=list)
    subtitles: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "ProjectState":
        data = json.loads(json_str)
        source = SourceMetadata(**data["source"]) if data.get("source") else None
        work_range = WorkRange(**data["work_range"]) if data.get("work_range") else None
        tracks = []
        for t_data in data.get("tracks", []):
            mask = MaskConfig(**t_data.get("mask", {}))
            kfs = [Keyframe(**kf) for kf in t_data.get("keyframes", [])]
            t = Track(
                id=t_data["id"],
                label=t_data.get("label", "遮蔽物件"),
                type=t_data.get("type", "manual"),
                enabled=t_data.get("enabled", True),
                mask=mask,
                keyframes=kfs
            )
            tracks.append(t)
        return cls(
            version=data.get("version", 1),
            source=source,
            work_range=work_range,
            tracks=tracks,
            subtitles=data.get("subtitles", [])
        )
