"""raw JSON을 Markdown으로 렌더링한다.

raw/{id}.json이 데이터의 원본이고 .md는 그 렌더링 결과다.
화자 이름을 바꾸면 md를 파싱해 고치는 게 아니라 여기서 다시 만든다.
"""
import re

PRICE_KRW_PER_HOUR = 1000  # 리턴제로 종량제(10시간 무료 이후)
WARNING = "> 자동 생성된 전사본입니다. 인식 오류가 포함될 수 있으니 중요한 내용은 원본을 확인하세요."


def format_timestamp(milliseconds: int) -> str:
    total_seconds = int(milliseconds) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def speaker_label(spk: int, speaker_map: dict[str, str]) -> str:
    """화자 이름이 지정되지 않았으면 '화자 1', '화자 2'로 표시한다(spk는 0부터)."""
    return speaker_map.get(str(spk)) or f"화자 {int(spk) + 1}"


def transcript_filename(date_iso: str, title: str) -> str:
    """2026-09-03-주간회의.md 형태. NotebookLM/탐색기에서 찾을 수 있어야 하므로 UUID를 쓰지 않는다."""
    date_part = date_iso[:10]
    safe_title = re.sub(r'[\\/:*?"<>|]', "", title).strip().replace(" ", "_")
    return f"{date_part}-{safe_title or 'untitled'}.md"


def audio_filename(date_iso: str, title: str) -> str:
    """추출된 오디오를 내려받을 때 쓸 이름. 전사본과 같은 규칙을 따른다."""
    return transcript_filename(date_iso, title).removesuffix(".md") + ".m4a"


def _yaml_str(value: str) -> str:
    """콜론·따옴표·개행이 있어도 안전하도록 YAML 큰따옴표 스칼라로 감싼다."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def _yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(_yaml_str(v) for v in values) + "]"


def render_markdown(*, meta: dict, raw: dict, speaker_map: dict[str, str]) -> str:
    utterances = raw.get("results", {}).get("utterances", [])
    duration_sec = float(meta.get("duration_sec") or 0)

    speakers_block = "\n".join(
        f'  "{key}": {_yaml_str(value)}' for key, value in sorted(speaker_map.items())
    )
    frontmatter = [
        "---",
        f"id: {meta['id']}",
        f"title: {_yaml_str(meta['title'])}",
        f"date: {meta['date']}",
        f"source: {_yaml_str(meta['source'])}",
        f'duration: "{format_timestamp(int(duration_sec * 1000))}"',
        "asr_provider: rtzr",
        "asr_model: sommers",
        "language: ko",
        f"estimated_cost_krw: {round(duration_sec / 3600 * PRICE_KRW_PER_HOUR)}",
    ]
    if meta.get("keywords"):
        frontmatter.append(f"keywords: {_yaml_list(meta['keywords'])}")
    if speakers_block:
        frontmatter.append("speakers:")
        frontmatter.append(speakers_block)
    frontmatter.append("---")

    if utterances:
        body = [
            f"**[{speaker_label(item['spk'], speaker_map)}] "
            f"{format_timestamp(item['start_at'])}** {item['msg'].strip()}"
            for item in utterances
        ]
    else:
        body = ["인식된 발화가 없습니다. 원본 오디오에 음성이 있는지 확인하세요."]

    return "\n".join([*frontmatter, "", WARNING, "", *body, ""])
