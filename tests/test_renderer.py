import json
from pathlib import Path

from app.renderer import format_timestamp, render_markdown, transcript_filename

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "rtzr_response.json").read_text(encoding="utf-8")
)

META = {
    "id": "01J8XYZ",
    "title": "주간회의",
    "date": "2026-09-03T14:22:00+09:00",
    "source": "D:/videos/weekly.mp4",
    "duration_sec": 3792.0,
    "keywords": ["개비공"],
}


def test_format_timestamp_converts_milliseconds():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(3000) == "00:00:03"
    assert format_timestamp(3723000) == "01:02:03"


def test_render_uses_default_speaker_labels():
    markdown = render_markdown(meta=META, raw=FIXTURE, speaker_map={})

    assert "**[화자 1] 00:00:03** 안녕하세요, 오늘 주간회의 시작하겠습니다." in markdown
    assert "**[화자 2] 00:00:11** 네, 지난주 진행상황부터 말씀드리겠습니다." in markdown


def test_render_applies_speaker_map():
    markdown = render_markdown(meta=META, raw=FIXTURE, speaker_map={"0": "김팀장", "1": "이대리"})

    assert "**[김팀장] 00:00:03**" in markdown
    assert "**[이대리] 00:00:11**" in markdown
    assert "화자 1" not in markdown


def test_render_writes_frontmatter_and_warning():
    markdown = render_markdown(meta=META, raw=FIXTURE, speaker_map={"0": "김팀장"})
    header = markdown.split("---")[1]

    assert 'title: "주간회의"' in header
    assert "asr_provider: rtzr" in header
    assert "asr_model: sommers" in header
    assert "language: ko" in header
    assert 'duration: "01:03:12"' in header
    assert "estimated_cost_krw: 1053" in header
    assert "자동 생성된 전사본" in markdown


def test_render_escapes_yaml_special_characters_in_title():
    meta = {**META, "title": '기획: "2차" 회의'}
    markdown = render_markdown(meta=meta, raw=FIXTURE, speaker_map={})
    header = markdown.split("---")[1]

    assert 'title: "기획: \\"2차\\" 회의"' in header


def test_each_utterance_is_one_line():
    markdown = render_markdown(meta=META, raw=FIXTURE, speaker_map={})
    body = markdown.split("---", 2)[2]
    utterance_lines = [line for line in body.splitlines() if line.startswith("**[")]

    assert len(utterance_lines) == 3


def test_render_handles_empty_utterances():
    markdown = render_markdown(
        meta=META, raw={"status": "completed", "results": {"utterances": []}}, speaker_map={}
    )
    assert "인식된 발화가 없습니다" in markdown


def test_transcript_filename_uses_date_and_title():
    assert transcript_filename("2026-09-03T14:22:00+09:00", "주간회의") == "2026-09-03-주간회의.md"


def test_transcript_filename_strips_path_separators():
    name = transcript_filename("2026-09-03T14:22:00+09:00", "회의/기획 : 1차")
    assert "/" not in name and ":" not in name
    assert name.endswith(".md")
