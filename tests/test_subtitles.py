import pytest
from clipmask.ai.subtitles import SubtitleManager

def test_srt_generation_and_parsing(tmp_path):
    srt_file = str(tmp_path / "test.srt")
    subs = [
        {"start": 1.250, "end": 3.750, "text": "第一行字幕測試"},
        {"start": 4.000, "end": 6.500, "text": "第二行字幕測試\n包含換行"}
    ]
    SubtitleManager.generate_srt_file(subs, srt_file)
    
    parsed = SubtitleManager.parse_srt_file(srt_file)
    assert len(parsed) == 2
    assert abs(parsed[0]["start"] - 1.250) < 1e-3
    assert abs(parsed[0]["end"] - 3.750) < 1e-3
    assert parsed[0]["text"] == "第一行字幕測試"
    assert "包含換行" in parsed[1]["text"]
