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


def _project(source):
    return ProjectState(source=SourceMetadata(str(source), 64, 48, 10.0, 2.0), work_range=WorkRange(0.0, 2.0))


def test_export_rejects_source_and_hard_link(tmp_path):
    import pytest
    source = tmp_path / "來源 影片.mp4"
    _make_video(source)
    original = source.read_bytes()
    alias = tmp_path / "來源連結.mp4"
    os.link(source, alias)
    for target in (source, alias):
        assert not FastCopyExporter.export(str(source), 0, 1, str(target))
        assert source.read_bytes() == original
        with pytest.raises(ValueError, match="來源"):
            RenderExporter.render_export(_project(source), str(target))
        assert source.read_bytes() == original


def test_cancel_preserves_existing_destination(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "existing.mp4"
    _make_video(source)
    output.write_bytes(b"previous export")
    assert not RenderExporter.render_export(_project(source), str(output), should_cancel=lambda: True)
    assert output.read_bytes() == b"previous export"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing.mp4", "source.mp4"]


def test_failed_exports_preserve_destination_and_release_files(tmp_path, monkeypatch):
    import pytest
    from clipmask.export import exporter
    source, output = tmp_path / "source.mp4", tmp_path / "existing.mp4"
    _make_video(source)
    original = source.read_bytes()
    output.write_bytes(b"previous export")
    real_open = av.open
    def failing_open(path, *args, **kwargs):
        if kwargs.get("mode") == "w":
            raise RuntimeError("模擬編碼器失敗")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(exporter.av, "open", failing_open)
    assert not FastCopyExporter.export(str(source), 0, 1, str(output))
    with pytest.raises(RuntimeError, match="編碼器"):
        RenderExporter.render_export(_project(source), str(output))
    assert source.read_bytes() == original
    assert output.read_bytes() == b"previous export"
    source.rename(tmp_path / "closed.mp4")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["closed.mp4", "existing.mp4"]


def test_render_failure_after_writing_preserves_destination(tmp_path):
    import pytest
    source, output = tmp_path / "source.mp4", tmp_path / "existing.mp4"
    _make_video(source)
    output.write_bytes(b"previous export")
    def fail_progress(_):
        raise RuntimeError("模擬中途失敗")
    with pytest.raises(RuntimeError, match="中途"):
        RenderExporter.render_export(_project(source), str(output), progress_callback=fail_progress)
    assert output.read_bytes() == b"previous export"
    source.rename(tmp_path / "closed.mp4")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["closed.mp4", "existing.mp4"]


def test_success_replaces_destination_and_keeps_source(tmp_path):
    source, output = tmp_path / "來源 影片.mp4", tmp_path / "完成 輸出.mp4"
    _make_video(source)
    original = source.read_bytes()
    for exporter in (lambda: FastCopyExporter.export(str(source), 0, 1, str(output)),
                     lambda: RenderExporter.render_export(_project(source), str(output))):
        output.write_bytes(b"previous export")
        assert exporter()
        assert source.read_bytes() == original
        with av.open(str(output)) as container:
            assert sum(1 for _ in container.decode(video=0)) > 0
        assert len(list(tmp_path.iterdir())) == 2


def test_final_progress_failure_keeps_previous_destination(tmp_path):
    import pytest
    source, output = tmp_path / "source.mp4", tmp_path / "existing.mp4"
    _make_video(source)
    output.write_bytes(b"previous export")
    def fail_at_completion(percent):
        if percent == 100:
            raise RuntimeError("模擬完成回報失敗")
    with pytest.raises(RuntimeError, match="完成回報"):
        RenderExporter.render_export(_project(source), str(output), progress_callback=fail_at_completion)
    assert output.read_bytes() == b"previous export"
    assert len(list(tmp_path.iterdir())) == 2
