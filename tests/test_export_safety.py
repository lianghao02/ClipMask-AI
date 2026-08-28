import os
import av
import numpy as np
from clipmask.export.exporter import FastCopyExporter, RenderExporter
from clipmask.models.project import ProjectState, SourceMetadata, WorkRange


def _make_video(path, frame_count=20):
    container = av.open(str(path), "w")
    stream = container.add_stream("h264", rate=10)
    stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
    for index in range(frame_count):
        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), index, dtype=np.uint8), format="rgb24")
        for packet in stream.encode(frame): container.mux(packet)
    for packet in stream.encode(): container.mux(packet)
    container.close()


def test_fast_copy_export_is_decodable(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "clip.mp4"
    _make_video(source, 30)
    assert FastCopyExporter.export(str(source), 0.5, 1.5, str(output))
    container = av.open(str(output))
    assert sum(1 for _ in container.decode(video=0)) > 0
    container.close()


def test_render_cancel_removes_partial_output(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "cancelled.mp4"
    _make_video(source, 20)
    project = ProjectState(source=SourceMetadata(str(source), 64, 48, 10.0, 2.0), work_range=WorkRange(0.0, 2.0))
    assert not RenderExporter.render_export(project, str(output), should_cancel=lambda: True)
    assert not os.path.exists(output)
