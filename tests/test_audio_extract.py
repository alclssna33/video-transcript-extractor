import subprocess

import pytest

from app.audio_extract import (
    MAX_DURATION_SEC,
    ExtractError,
    TooLongError,
    extract_audio,
    probe_duration,
)


@pytest.fixture
def sample_video(tmp_path):
    """무음 3초짜리 실제 mp4를 만든다. ffmpeg를 mock하지 않는다."""
    path = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=3",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True, check=True,
    )
    return path


def test_probe_duration_returns_seconds(sample_video):
    assert probe_duration(sample_video) == pytest.approx(3.0, abs=0.5)


def test_probe_duration_raises_on_missing_file(tmp_path):
    with pytest.raises(ExtractError):
        probe_duration(tmp_path / "없는파일.mp4")


def test_extract_audio_produces_m4a(sample_video, tmp_path):
    output = extract_audio(sample_video, tmp_path / "out.m4a")

    assert output.exists()
    assert output.suffix == ".m4a"
    assert output.stat().st_size > 0
    # 영상 트랙이 빠지므로 원본보다 작아야 한다
    assert output.stat().st_size < sample_video.stat().st_size


def test_extract_audio_rejects_too_long(sample_video, tmp_path, monkeypatch):
    monkeypatch.setattr("app.audio_extract.MAX_DURATION_SEC", 1)
    with pytest.raises(TooLongError) as exc:
        extract_audio(sample_video, tmp_path / "out.m4a")
    assert "4시간" in str(exc.value) or "초과" in str(exc.value)


def test_max_duration_is_four_hours():
    assert MAX_DURATION_SEC == 4 * 3600
