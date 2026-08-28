import pytest
import numpy as np
import os
import av
from clipmask.models.project import ProjectState, Track, Keyframe, MaskConfig, WorkRange, SourceMetadata
from clipmask.media.source import VideoSource
from clipmask.export.exporter import RenderExporter

def test_full_pipeline_render_export(tmp_path):
    src_video = str(tmp_path / "source.mp4")
    out_video = str(tmp_path / "output_redacted.mp4")
    
    # 建立 2 秒 30fps 測試影片 (60 幀，160x120)
    container = av.open(src_video, mode="w")
    stream = container.add_stream("h264", rate=30)
    stream.width = 160
    stream.height = 120
    stream.pix_fmt = "yuv420p"
    
    for i in range(60):
        # 繪製純白底，中央放一個 40x40 黑色方塊（模擬人臉）
        img = np.full((120, 160, 3), 255, dtype=np.uint8)
        img[40:80, 60:100] = 0
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()

    # 建立專案與 Track (0.0s -> 2.0s 遮蔽中央區域)
    source = VideoSource(src_video)
    project = ProjectState(
        source=source.metadata,
        work_range=WorkRange(in_time=0.0, out_time=2.0),
        tracks=[
            Track(
                id="t1",
                label="測試人臉",
                mask=MaskConfig(style="mosaic", strength=10, padding=0.1),
                keyframes=[
                    Keyframe(time=0.0, pts=0, rect_px=(60, 40, 40, 40)),
                    Keyframe(time=2.0, pts=source.time_to_pts(2.0), rect_px=(60, 40, 40, 40))
                ]
            )
        ]
    )
    source.close()

    # 執行匯出壓制
    success = RenderExporter.render_export(project, out_video)
    assert success is True
    assert os.path.exists(out_video)
    assert os.path.getsize(out_video) > 0
