"""
ClipMask-AI Exporter (支援遮蔽 + 聽打字幕壓制)
"""
import os
import cv2
import numpy as np
import av
from typing import Callable, Optional
from ..models.project import ProjectState
from ..media.source import VideoSource
from ..track.evaluator import TrackEvaluator
from ..ai.subtitles import SubtitleManager

class FastCopyExporter:
    @staticmethod
    def export(source_path: str, in_time: float, out_time: float, output_path: str) -> bool:
        """純原生 PyAV 無損快速串流剪輯 (零外部依賴、免裝 ffmpeg、2 秒秒出)"""
        try:
            in_container = av.open(source_path)
            out_container = av.open(output_path, mode="w")

            streams_map = {}
            # 複製視訊與音訊串流模板
            for in_stream in in_container.streams:
                if in_stream.type in ("video", "audio"):
                    out_stream = out_container.add_stream_from_template(in_stream)
                    streams_map[in_stream] = out_stream

            if not streams_map:
                in_container.close()
                out_container.close()
                return False

            v_stream = in_container.streams.video[0] if in_container.streams.video else None
            if v_stream:
                target_pts = int(round(in_time / float(v_stream.time_base)))
                in_container.seek(target_pts, any_frame=False, backward=True, stream=v_stream)
                end_pts = int(round(out_time / float(v_stream.time_base)))
            else:
                end_pts = None

            for packet in in_container.demux(list(streams_map.keys())):
                if packet.dts is None:
                    continue
                if packet.stream == v_stream and packet.pts is not None and end_pts is not None:
                    if packet.pts > end_pts:
                        break
                packet.stream = streams_map[packet.stream]
                out_container.mux(packet)

            in_container.close()
            out_container.close()
            return True
        except Exception:
            return False

class RenderExporter:
    @staticmethod
    def apply_mosaic_or_blur(frame_rgb: np.ndarray, rect: tuple, style: str, strength: int) -> np.ndarray:
        x, y, w, h = rect
        fh, fw = frame_rgb.shape[:2]
        
        x = max(0, min(fw - 1, x))
        y = max(0, min(fh - 1, y))
        w = max(1, min(fw - x, w))
        h = max(1, min(fh - y, h))
        
        roi = frame_rgb[y:y+h, x:x+w]
        if roi.size == 0:
            return frame_rgb

        if style == "mosaic":
            block_size = max(4, strength)
            rw = max(1, w // block_size)
            rh = max(1, h // block_size)
            small = cv2.resize(roi, (rw, rh), interpolation=cv2.INTER_LINEAR)
            masked = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        else:
            k = max(3, strength | 1)
            masked = cv2.GaussianBlur(roi, (k, k), 0)

        frame_rgb[y:y+h, x:x+w] = masked
        return frame_rgb

    @staticmethod
    def render_export(
        project: ProjectState,
        output_path: str,
        progress_callback: Optional[Callable[[int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> bool:
        if not project.source:
            return False

        source = VideoSource(project.source.path)
        in_t = project.work_range.in_time if project.work_range else 0.0
        out_t = project.work_range.out_time if project.work_range else source.duration
        total_duration = max(0.1, out_t - in_t)

        output_container = av.open(output_path, mode="w")
        cancelled = False
        stream = output_container.add_stream("h264", rate=int(round(source.fps)))
        stream.width = source.width
        stream.height = source.height
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "20", "preset": "fast"}

        source.seek_exact(in_t)
        
        frame_idx = 0
        while True:
            if should_cancel and should_cancel():
                cancelled = True
                break
            frame = source.read_next_frame()
            if frame is None:
                break
            
            cur_t = source.current_time
            if cur_t > out_t + 0.05:
                break
            
            # 1. 應用遮蔽
            evaluated = TrackEvaluator.evaluate_all_tracks_at(project.tracks, cur_t, source.width, source.height)
            for track, rect in evaluated:
                frame = RenderExporter.apply_mosaic_or_blur(frame, rect, track.mask.style, track.mask.strength)

            # 2. 應用聽打字幕
            if project.subtitles:
                sub_text = SubtitleManager.get_active_subtitle_at(project.subtitles, cur_t)
                if sub_text:
                    frame = SubtitleManager.draw_subtitle_on_image(frame, sub_text)

            av_frame = av.VideoFrame.from_ndarray(frame, format="rgb24")
            for packet in stream.encode(av_frame):
                output_container.mux(packet)

            frame_idx += 1
            if progress_callback and total_duration > 0:
                pct = int(min(100, max(0, ((cur_t - in_t) / total_duration) * 100)))
                progress_callback(pct)

        if not cancelled:
            for packet in stream.encode():
                output_container.mux(packet)

        output_container.close()
        source.close()

        if cancelled:
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
            return False
        
        if progress_callback:
            progress_callback(100)
            
        return True
