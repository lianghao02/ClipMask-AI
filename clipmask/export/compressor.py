"""
ClipMask-AI 獨立影片容量壓縮模組（純原生 PyAV libx264 + AAC，不依賴外部 ffmpeg.exe）
支援依據目標檔案大小（預設標準單層 DVD 4.7GB 光碟安全上限 4,300 MB）推算最適位元率與解析度。
"""
import math
import os
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Optional, Tuple
import av

from .exporter import _export_destination, _get_canonical_audio_layout

# 標準單層 4.7 GB DVD（SI 十進位 ≈ 4,482 MiB），預留 UDF/ISO 燒錄表頭安全值為 4,300 MB
DEFAULT_DVD_TARGET_MB = 4300
DVD_DL_TARGET_MB = 7900
CLOUD_SHARE_TARGET_MB = 500


@dataclass
class VideoProbeInfo:
    path: str
    file_size_bytes: int
    duration_sec: float
    width: int
    height: int
    fps: float
    has_audio: bool
    source_bitrate_bps: int


@dataclass
class CompressionPlan:
    source_size_bytes: int
    target_mb: int
    target_bytes: int
    estimated_output_bytes: int
    video_bitrate_kbps: int
    audio_bitrate_kbps: int
    output_width: int
    output_height: int
    already_smaller: bool
    downscaled: bool
    quality_note: str


def format_file_size(num_bytes: int) -> str:
    """同時以親切易讀的 GB / MB 格式顯示檔案大小（對齊 Windows 檔案總管 1024 換算）"""
    if num_bytes <= 0:
        return "0 MB"
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        gb = mb / 1024
        return f"{gb:.2f} GB（{mb:,.0f} MB）"
    if mb >= 10:
        return f"{mb:,.1f} MB"
    return f"{mb:.2f} MB"


def probe_video_info(video_path: str) -> VideoProbeInfo:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"找不到檔案：{video_path}")
    file_size_bytes = os.path.getsize(video_path)
    with av.open(video_path) as container:
        if not container.streams.video:
            raise ValueError("此檔案不含視訊軌，無法壓縮。")
        v_stream = container.streams.video[0]
        width = int(v_stream.width or 0)
        height = int(v_stream.height or 0)
        fps = float(v_stream.average_rate) if v_stream.average_rate else 30.0
        if fps <= 0:
            fps = 30.0

        duration_sec = 0.0
        if container.duration is not None:
            duration_sec = float(container.duration / av.time_base)
        elif v_stream.duration is not None and v_stream.time_base is not None:
            duration_sec = float(v_stream.duration * v_stream.time_base)
        duration_sec = max(0.5, duration_sec)

        has_audio = len(container.streams.audio) > 0
        source_bitrate_bps = int((file_size_bytes * 8) / duration_sec)
        return VideoProbeInfo(
            path=video_path,
            file_size_bytes=file_size_bytes,
            duration_sec=duration_sec,
            width=width,
            height=height,
            fps=fps,
            has_audio=has_audio,
            source_bitrate_bps=source_bitrate_bps,
        )


def _scale_dimensions_even(width: int, height: int, max_height: int) -> Tuple[int, int]:
    if height <= max_height or height <= 0 or width <= 0:
        return max(2, width - (width % 2)), max(2, height - (height % 2))
    ratio = max_height / float(height)
    new_h = max(2, int(round(height * ratio)))
    new_w = max(2, int(round(width * ratio)))
    new_w -= new_w % 2
    new_h -= new_h % 2
    return max(2, new_w), max(2, new_h)


def calculate_compression_plan(
    info: VideoProbeInfo,
    target_mb: int = DEFAULT_DVD_TARGET_MB,
    auto_downscale: bool = True,
) -> CompressionPlan:
    target_mb = max(1, int(target_mb))
    target_bytes = target_mb * 1024 * 1024
    # 預留 10% 給 MP4 封裝標頭與單次 ABR 動態波動，提高長片壓進目標容量的命中率
    budget_bytes = int(target_bytes * 0.90)
    duration = max(0.5, info.duration_sec)
    total_target_kbps = max(150, int((budget_bytes * 8) / duration / 1000))

    if not info.has_audio:
        audio_kbps = 0
    elif total_target_kbps >= 650:
        audio_kbps = 128
    elif total_target_kbps >= 320:
        audio_kbps = 96
    else:
        audio_kbps = 64

    video_kbps = max(120, total_target_kbps - audio_kbps)
    # 若目標容量反而比原檔還大，不需要灌水膨脹碼率
    already_smaller = info.file_size_bytes <= target_bytes
    if already_smaller and info.source_bitrate_bps > 0:
        src_v_kbps = max(120, int(info.source_bitrate_bps / 1000) - audio_kbps)
        video_kbps = min(video_kbps, src_v_kbps)

    out_w, out_h = _scale_dimensions_even(info.width, info.height, max_height=max(2, info.height))
    downscaled = False
    if auto_downscale and not already_smaller:
        if out_h > 720 and video_kbps < 1600:
            out_w, out_h = _scale_dimensions_even(info.width, info.height, max_height=720)
            downscaled = (out_w, out_h) != (info.width, info.height)
        if out_h > 480 and video_kbps < 650:
            out_w, out_h = _scale_dimensions_even(info.width, info.height, max_height=480)
            downscaled = (out_w, out_h) != (info.width, info.height)

    est_total_bps = (video_kbps + audio_kbps) * 1000
    est_bytes = min(target_bytes, int((est_total_bps * duration) / 8 * 1.03))
    if already_smaller:
        quality_note = f"目前檔案（{format_file_size(info.file_size_bytes)}）已經小於目標 {target_mb:,} MB，不一定需要壓縮。"
    elif video_kbps >= 3500:
        quality_note = f"畫質充裕（約 {video_kbps:,} kbps），幾乎看不出畫質減損。"
    elif video_kbps >= 1500:
        quality_note = f"畫質良好（約 {video_kbps:,} kbps），適合一般存檔與播放。"
    elif video_kbps >= 700:
        quality_note = f"中度壓縮（約 {video_kbps:,} kbps），建議勾選自動調整解析度以減少塊狀雜訊。"
    else:
        quality_note = f"極限高壓縮（約 {video_kbps:,} kbps），片長較長、畫面細節會較柔和。"

    return CompressionPlan(
        source_size_bytes=info.file_size_bytes,
        target_mb=target_mb,
        target_bytes=target_bytes,
        estimated_output_bytes=max(1, est_bytes),
        video_bitrate_kbps=video_kbps,
        audio_bitrate_kbps=audio_kbps,
        output_width=out_w,
        output_height=out_h,
        already_smaller=already_smaller,
        downscaled=downscaled,
        quality_note=quality_note,
    )


class VideoCompressor:
    @staticmethod
    def compress(
        source_path: str,
        output_path: str,
        target_mb: int = DEFAULT_DVD_TARGET_MB,
        auto_downscale: bool = True,
        progress_callback: Optional[Callable[[int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> bool:
        info = probe_video_info(source_path)
        plan = calculate_compression_plan(info, target_mb=target_mb, auto_downscale=auto_downscale)

        with _export_destination(source_path, output_path) as temporary:
            cancelled = False
            with av.open(source_path) as in_container:
                in_video = in_container.streams.video[0]
                in_audio = in_container.streams.audio[0] if in_container.streams.audio else None

                fps_fraction = in_video.average_rate or Fraction(30, 1)
                if float(fps_fraction) <= 0:
                    fps_fraction = Fraction(30, 1)

                with av.open(temporary, mode="w") as out_container:
                    out_video = out_container.add_stream("libx264", rate=fps_fraction)
                    out_video.width = plan.output_width
                    out_video.height = plan.output_height
                    out_video.pix_fmt = "yuv420p"
                    out_video.bit_rate = plan.video_bitrate_kbps * 1000
                    out_video.time_base = Fraction(1, 1000)
                    out_video.options = {
                        "preset": "fast",
                        "maxrate": f"{int(plan.video_bitrate_kbps * 1.15)}k",
                        "bufsize": f"{int(plan.video_bitrate_kbps * 1.5)}k",
                    }

                    out_audio = None
                    resampler = None
                    if in_audio is not None and plan.audio_bitrate_kbps > 0:
                        audio_rate = in_audio.codec_context.sample_rate or 48000
                        audio_layout = _get_canonical_audio_layout(in_audio)
                        out_audio = out_container.add_stream("aac", rate=audio_rate)
                        out_audio.layout = audio_layout
                        out_audio.bit_rate = plan.audio_bitrate_kbps * 1000
                        resampler = av.AudioResampler(
                            format="fltp", layout=audio_layout, rate=audio_rate
                        )

                    first_video_time = None
                    last_video_pts = -1
                    audio_samples_written = 0

                    decode_streams = [in_video] + ([in_audio] if (in_audio and out_audio) else [])
                    for packet in in_container.demux(decode_streams):
                        if should_cancel and should_cancel():
                            cancelled = True
                            break

                        for frame in packet.decode():
                            if should_cancel and should_cancel():
                                cancelled = True
                                break

                            if isinstance(frame, av.VideoFrame):
                                cur_t = float(frame.pts * in_video.time_base) if frame.pts is not None else 0.0
                                if first_video_time is None:
                                    first_video_time = cur_t
                                rel_ms = max(0, int(round((cur_t - first_video_time) * 1000)))
                                last_video_pts = max(last_video_pts + 1, rel_ms)

                                reformatted = frame.reformat(
                                    width=plan.output_width,
                                    height=plan.output_height,
                                    format="yuv420p",
                                )
                                reformatted.pts = last_video_pts
                                reformatted.time_base = Fraction(1, 1000)
                                for out_pkt in out_video.encode(reformatted):
                                    out_container.mux(out_pkt)

                                if progress_callback and info.duration_sec > 0:
                                    pct = int(min(99, max(0, ((cur_t - first_video_time) / info.duration_sec) * 100)))
                                    progress_callback(pct)

                            elif isinstance(frame, av.AudioFrame) and out_audio and resampler:
                                for resampled in resampler.resample(frame):
                                    arr = resampled.to_ndarray()
                                    clipped = av.AudioFrame.from_ndarray(
                                        arr, format="fltp", layout=out_audio.layout.name
                                    )
                                    clipped.sample_rate = out_audio.rate
                                    clipped.pts = audio_samples_written
                                    clipped.time_base = Fraction(1, out_audio.rate)
                                    audio_samples_written += clipped.samples
                                    for out_pkt in out_audio.encode(clipped):
                                        out_container.mux(out_pkt)

                        if cancelled:
                            break

                    if not cancelled:
                        for out_pkt in out_video.encode():
                            out_container.mux(out_pkt)
                        if out_audio is not None:
                            for out_pkt in out_audio.encode():
                                out_container.mux(out_pkt)

            if cancelled:
                return False
            if progress_callback:
                progress_callback(100)
            os.replace(temporary, output_path)
            return True
