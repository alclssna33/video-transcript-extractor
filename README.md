# 영상 대본 추출기

로컬 영상 파일과 온라인 영상 URL에서 **화자가 구분된 한국어 전사본(.md)** 을 만든다.
결과 파일은 NotebookLM에 소스로 업로드해 지식화·질의응답에 사용한다.

## 빠른 시작 (Windows)

1. **Python** (https://www.python.org/downloads/ — 설치 시 "Add python.exe to PATH" 체크) 와
   **ffmpeg** (https://www.gyan.dev/ffmpeg/builds/ — "release essentials" 다운로드 후
   압축 풀고 `bin` 폴더를 PATH에 추가) 를 먼저 설치한다
2. **`실행하기.bat`를 더블클릭**한다. 처음 실행할 때만 자동으로 필요한 걸 설치하고,
   설치가 끝나면 브라우저가 자동으로 열린다. 다음부터는 그냥 다시 더블클릭하면 된다
3. RTZR 자격 증명(스크립트 추출에만 필요)은 **설정 화면**에서 입력한다
   - https://developers.rtzr.ai/signup 가입 → https://developers.rtzr.ai/console/ 에서 발급
     (가입 시 10시간 무료)
   - **오디오 추출만 할 거라면 자격 증명 없이도 바로 쓸 수 있다**

껐다 켜려면 `실행하기.bat`을 실행한 검은 콘솔 창을 닫으면 된다.

## 직접 설치 (개발자용, 다른 OS 포함)

`실행하기.bat`이 하는 일을 손으로 하는 방법이다.

0. **Python 3.10 이상** (코드 전반에서 `X | None` 타입 문법을 사용함)
1. **ffmpeg 설치** (ffprobe 포함) 후 PATH에 추가 — `ffmpeg -version`으로 확인

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

2. **RTZR 자격 증명** — **스크립트 추출을 쓸 때만 필요하다.**
   오디오 추출만 할 거라면 이 단계를 건너뛰어도 된다.
   - https://developers.rtzr.ai/signup 가입 → https://developers.rtzr.ai/console/ 에서
     client_id / client_secret 발급 (가입 시 10시간 무료)
   - 앱을 실행한 뒤 **설정 화면에서 입력**하면 된다. `.env` 파일을 직접 편집할 필요 없다
   - (원하면 `.env.example`을 `.env`로 복사해 채워도 된다 — 설정 화면 값이 우선한다)

```bash
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload --port 8000
```

http://localhost:8000 접속 후:
- 영상 파일 경로나 URL을 입력하고 제출하거나
- `data/inbox/` 폴더에 영상을 넣고 앱을 재시작한다

결과는 `data/transcripts/*.md`에 저장된다. 이 파일을 NotebookLM에 업로드하면 된다.

## 처리 범위

제출할 때 두 가지 중 고른다.

| | 하는 일 | RTZR 자격 증명 |
|---|---|---|
| **스크립트까지** | 오디오 추출 → RTZR 전사 → 화자 구분된 `.md` | 필요 |
| **오디오만 추출** | 오디오 추출까지만 (`.m4a`) | **불필요** |

"오디오만 추출"로 만든 `.m4a`는 상세 화면에서 내려받아 **클로바노트 같은 외부 도구**에 올릴 수 있다
(클로바노트는 영상 업로드를 지원하지 않으므로 오디오로 변환해야 한다).
나중에 마음이 바뀌면 상세 화면의 **"이어서 스크립트 추출"** 버튼을 누르면 되고, 이때 오디오를
다시 뽑지 않는다.

## 알아둘 것

- 전사본은 **완성된 회의록이 아니라 검토가 필요한 초안**이다. 타임스탬프로 원본을 확인할 것
- 화자 이름은 상세 화면에서 지정할 수 있다. 저장하면 원본 JSON에서 .md를 다시 만들므로,
  **.md를 직접 편집한 뒤 이름을 바꾸면 편집 내용이 덮어써진다**
- 제한: 최대 4시간 / 2GB. 전사 결과는 RTZR 서버에 **3일만 보관**된다
- 고유명사 힌트(keywords)를 넣으면 이름·전문용어 오인식이 줄어든다
- 비용: 10시간 무료(1회성) 후 시간당 1,000원
- 추출된 오디오(`.m4a`)는 자동으로 지워지지 않는다. 상세 화면에서 직접 삭제할 수 있다
- 설정 화면에 저장한 값은 `.env`보다 우선한다. **"저장된 값 지우기"** 를 누르면 `.env`로 되돌아간다

## 다른 사람에게 줄 때

⚠️ **폴더를 그대로 복사해 주면 `.env`의 자격 증명과 `data/`의 전사본이 함께 넘어간다.**
`.gitignore`는 git에만 적용되고 폴더 복사에는 아무 효과가 없다.

깨끗한 사본을 만드는 방법:

```bash
git archive --format=zip --output=../영상대본추출기.zip HEAD
```

이렇게 하면 git이 추적하는 파일만 압축된다 — `.env`도, `data/`도 들어가지 않는다.

직접 복사해서 주려면 전달 전에 이 둘을 반드시 지운다:
- `.env` (자격 증명)
- `data/` (전사본·오디오·작업 DB)

받는 사람은 위 "빠른 시작"을 따라 `실행하기.bat`을 더블클릭하고, 자기 RTZR 자격 증명을
**설정 화면**에서 입력하면 된다.

## 테스트

```bash
.venv/Scripts/python -m pytest -v
```
