import av
import numpy as np

from clipmask.ai.detector import FaceDetector
from clipmask.export.exporter import FastCopyExporter, RenderExporter
from clipmask.models.project import ProjectState, SourceMetadata, WorkRange, Track, Keyframe
from clipmask.track.coverage import CoverageAnalyzer


def _make_av_video(path, duration=3, fps=10, sample_rate=16000):
    container = av.open(str(path), "w")
    video = container.add_stream("h264", rate=fps)
    video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
    audio = container.add_stream("aac", rate=sample_rate)
    audio.layout = "mono"

    for index in range(duration * fps):
        image = np.full((48, 64, 3), index % 255, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        for packet in video.encode(frame):
            container.mux(packet)

    samples = np.zeros(duration * sample_rate, dtype=np.float32)
    samples[sample_rate // 2 :] = 0.25
    for start in range(0, len(samples), 1024):
        chunk = samples[start : start + 1024]
        if len(chunk) < 1024:
            chunk = np.pad(chunk, (0, 1024 - len(chunk)))
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="fltp", layout="mono")
        frame.sample_rate = sample_rate
        for packet in audio.encode(frame):
            container.mux(packet)

    for packet in video.encode():
        container.mux(packet)
    for packet in audio.encode():
        container.mux(packet)
    container.close()


def test_render_export_preserves_audio_with_trimmed_range(tmp_path):
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "redacted.mp4"
    _make_av_video(source_path)
    project = ProjectState(
        source=SourceMetadata(str(source_path), 64, 48, 10.0, 3.0),
        work_range=WorkRange(0.5, 2.5),
    )

    assert RenderExporter.render_export(project, str(output_path))
    container = av.open(str(output_path))
    assert container.streams.audio
    assert sum(frame.samples for frame in container.decode(audio=0)) > 0
    assert 1.6 <= float(container.duration / av.time_base) <= 2.4
    container.close()


def test_fast_copy_preserves_audio_and_rebases_output_duration(tmp_path):
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "cut.mp4"
    _make_av_video(source_path)

    assert FastCopyExporter.export(str(source_path), 0.5, 2.0, str(output_path))
    container = av.open(str(output_path))
    assert container.streams.audio
    assert sum(frame.samples for frame in container.decode(audio=0)) > 0
    assert 0.8 <= float(container.duration / av.time_base) <= 2.2
    container.close()


def test_coverage_marks_incomplete_tracks_as_critical():
    track = Track(id="face", keyframes=[Keyframe(time=1.0), Keyframe(time=6.0)])
    report = CoverageAnalyzer.analyze([track], 0.0, 8.0)
    assert report.critical


def test_ai_scan_uses_one_initial_seek():
    class FakeSource:
        duration = 1.0
        fps = 10.0
        time_base = 0.1

        def __init__(self):
            self.seek_calls = 0
            self.current_time = 0.0
            self.current_pts = 0
            self._frame_index = 0

        def seek_exact(self, _seconds):
            self.seek_calls += 1
            return np.zeros((8, 8, 3), dtype=np.uint8)

        def read_next_frame(self):
            if self._frame_index > 10:
                return None
            self.current_time = self._frame_index / 10.0
            self.current_pts = self._frame_index
            self._frame_index += 1
            return np.zeros((8, 8, 3), dtype=np.uint8)

    source = FakeSource()
    detector = FaceDetector.__new__(FaceDetector)
    detector.detect_in_frame = lambda _frame: []
    assert detector.scan_work_range(source, 0.0, 1.0, step_sec=0.25) == []
    assert source.seek_calls == 1
