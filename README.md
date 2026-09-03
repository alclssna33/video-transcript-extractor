# 영상 대본 추출기

로컬 영상 파일과 온라인 영상 URL에서 **화자가 구분된 한국어 전사본(.md)** 을 만든다.
결과 파일은 NotebookLM에 소스로 업로드해 지식화·질의응답에 사용한다.

## 준비

1. **ffmpeg 설치** (ffprobe 포함) 후 PATH에 추가 — `ffmpeg -version`으로 확인
2. **RTZR 자격 증명 발급**: https://developers.rtzr.ai/signup 가입 후
   https://developers.rtzr.ai/console/ 에서 client_id / client_secret 발급 (10시간 무료)
3. `.env.example`을 `.env`로 복사하고 값 채우기

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env    # 값을 채운다
```

## 실행

```bash
.venv/Scripts/python -m uvicorn app.main:create_app --factory --reload --port 8000
```

http://localhost:8000 접속 후:
- 영상 파일 경로나 URL을 입력하고 제출하거나
- `data/inbox/` 폴더에 영상을 넣고 앱을 재시작한다

결과는 `data/transcripts/*.md`에 저장된다. 이 파일을 NotebookLM에 업로드하면 된다.

## 알아둘 것

- 전사본은 **완성된 회의록이 아니라 검토가 필요한 초안**이다. 타임스탬프로 원본을 확인할 것
- 화자 이름은 상세 화면에서 지정할 수 있다. 저장하면 원본 JSON에서 .md를 다시 만들므로,
  **.md를 직접 편집한 뒤 이름을 바꾸면 편집 내용이 덮어써진다**
- 제한: 최대 4시간 / 2GB. 전사 결과는 RTZR 서버에 **3일만 보관**된다
- 고유명사 힌트(keywords)를 넣으면 이름·전문용어 오인식이 줄어든다
- 비용: 10시간 무료 후 시간당 1,000원

## 테스트

```bash
.venv/Scripts/python -m pytest -v
```
