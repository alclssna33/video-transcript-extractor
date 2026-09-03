"""ffmpeg / ffprobe / yt-dlp 래퍼.

RTZR이 받는 포맷(mp4, m4a, mp3, amr, flac, wav)으로만 출력한다.
업로드 크기를 줄이는 것이 목적이므로 mono AAC 48kbps로 압축한다(1시간 ≈ 21MB).
"""
import json
import subprocess
import sys
from pathlib import Path

MAX_DURATION_SEC = 4 * 3600          # RTZR 제한: 최대 4시간
MAX_UPLOAD_BYTES = 2 * 1024**3       # RTZR 제한: 최대 2GB


class ExtractError(Exception):
    """ffmpeg/ffprobe/yt-dlp 실행 실패. stderr 원문을 그대로 담는다."""


class TooLongError(ExtractError):
    """RTZR의 4시간 제한을 초과."""


def _run(command: list[str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError as exc:
        raise ExtractError(f"{command[0]} 를 찾을 수 없습니다. 설치 후 PATH에 추가하세요.") from exc
    if result.returncode != 0:
        raise ExtractError(result.stderr.strip() or f"{command[0]} 실행 실패")
    return result


def probe_duration(path: Path) -> float:
    """오디오/영상 길이를 초 단위로 반환."""
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ExtractError(f"길이를 읽을 수 없습니다: {path}") from exc


def extract_audio(source: Path, dest: Path) -> Path:
    """영상/오디오에서 mono AAC(m4a) 트랙만 추출한다."""
    duration = probe_duration(source)
    if duration > MAX_DURATION_SEC:
        raise TooLongError(
            f"길이 {duration / 3600:.1f}시간으로 RTZR 제한(4시간)을 초과했습니다."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(source),
        "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "48k",
        str(dest),
    ])

    if dest.stat().st_size > MAX_UPLOAD_BYTES:
        raise ExtractError("추출된 오디오가 2GB를 초과했습니다.")
    return dest


def download_url(url: str, dest_dir: Path) -> Path:
    """yt-dlp로 영상을 내려받고 저장된 파일 경로를 반환한다."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    template = str(dest_dir / "%(id)s.%(ext)s")
    _run([sys.executable, "-m", "yt_dlp", "-o", template, "--no-playlist",
          "--print-to-file", "%(filepath)s", str(dest_dir / "_last_path.txt"), url])

    pointer = dest_dir / "_last_path.txt"
    if not pointer.exists():
        raise ExtractError("yt-dlp가 다운로드 경로를 보고하지 않았습니다.")
    downloaded = Path(pointer.read_text(encoding="utf-8").strip().splitlines()[-1])
    pointer.unlink(missing_ok=True)

    if not downloaded.exists():
        raise ExtractError(f"다운로드된 파일을 찾을 수 없습니다: {downloaded}")
    return downloaded
