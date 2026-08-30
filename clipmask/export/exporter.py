"""
ClipMask-AI Exporter (支援遮蔽 + 聽打字幕壓制)
"""
import os
import cv2
import numpy as np
import av
from fractions import Fraction
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

            stream_base_dts = {}
            for packet in in_container.demux(list(streams_map.keys())):
                if packet.dts is None:
                    continue
                source_stream = packet.stream
                packet_time = float(packet.pts * source_stream.time_base) if packet.pts is not None else float(packet.dts * source_stream.time_base)
                packet_duration = float(packet.duration * source_stream.time_base) if packet.duration else 0.0
                if packet_time >= out_time or packet_time + packet_duration > out_time:
                    continue

                # Stream copy 必須從關鍵影格開始；各串流各自將 DTS 歸零，
                # 保留 PTS-DTS 偏移以維持 B-frame 的顯示順序。
                if source_stream not in stream_base_dts:
                    stream_base_dts[source_stream] = packet.dts
                base_dts = stream_base_dts[source_stream]
                packet.pts = packet.pts - base_dts if packet.pts is not None else None
                packet.dts = packet.dts - base_dts
                packet.stream = streams_map[source_stream]
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

        # 壓制匯出不應遺失原始音軌。音訊另外解碼、裁切、重採樣並以
        # 從 0 開始的時間戳寫入，避免來源 PTS 造成輸出 A/V 不同步。
        audio_input = None
        audio_stream = None
        audio_output = None
        audio_resampler = None
        audio_samples_written = 0
        try:
            audio_input = av.open(project.source.path)
            if audio_input.streams.audio:
                audio_stream = audio_input.streams.audio[0]
                audio_rate = audio_stream.codec_context.sample_rate or 48000
                audio_layout = audio_stream.layout.name if audio_stream.layout else "stereo"
                audio_output = output_container.add_stream("aac", rate=audio_rate)
                audio_output.layout = audio_layout
                audio_resampler = av.AudioResampler(
                    format="fltp", layout=audio_layout, rate=audio_rate
                )
        except Exception:
            if audio_input is not None:
                audio_input.close()
            audio_input = None
            audio_stream = None
            audio_output = None
            audio_resampler = None

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
            if cur_t < in_t - 0.001:
                continue
            
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

        if not cancelled and audio_input and audio_stream and audio_output and audio_resampler:
            audio_rate = audio_output.rate
            for input_frame in audio_input.decode(audio_stream):
                if input_frame.pts is None:
                    continue
                for resampled in audio_resampler.resample(input_frame):
                    frame_start = float(resampled.pts * resampled.time_base) if resampled.pts is not None else 0.0
                    frame_end = frame_start + (resampled.samples / audio_rate)
                    if frame_end <= in_t:
                        continue
                    if frame_start >= out_t:
                        break

                    start_sample = max(0, int(round((in_t - frame_start) * audio_rate)))
                    end_sample = min(resampled.samples, int(round((out_t - frame_start) * audio_rate)))
                    if end_sample <= start_sample:
                        continue

                    samples = resampled.to_ndarray()[:, start_sample:end_sample]
                    clipped = av.AudioFrame.from_ndarray(samples, format="fltp", layout=audio_output.layout.name)
                    clipped.sample_rate = audio_rate
                    clipped.pts = audio_samples_written
                    clipped.time_base = Fraction(1, audio_rate)
                    audio_samples_written += clipped.samples
                    for packet in audio_output.encode(clipped):
                        output_container.mux(packet)

        if not cancelled:
            for packet in stream.encode():
                output_container.mux(packet)
            if audio_output:
                for packet in audio_output.encode():
                    output_container.mux(packet)

        output_container.close()
        source.close()
        if audio_input:
            audio_input.close()

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
