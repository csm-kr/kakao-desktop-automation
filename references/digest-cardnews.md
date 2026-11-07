# 빠른 대화 요약과 이미지 카드뉴스

이 경로는 수집한 텍스트를 에이전트가 요약하고 이미지를 생성하는 작업을 연결한다. Python 스크립트가 언어 모델이나 이미지 API를 자동 호출하는 기능은 아니다. 메시지 전송·메일·예약·공개 게시와 연결하지 않는다.

## 먼저 요약, 이미지는 뒤에

- 같은 수집 범위를 다시 요청받으면 비공개 캡처를 재사용한다. 새 정보를 요구하면 명시한 방만 새로 수집한다. 기존 캡처를 최신 대화로 표시하지 않는다.
- **AI 소식 + 카톡 대화 내용**을 함께 요약한다. 전체 핵심과 주제별 내용·활용 포인트를 먼저 제공한다. 공식 소식, 참여자 경험/의견, 질문·모집을 구분한다. 확인 가능한 발표가 없으면 억지로 채우지 않는다.
- 이미지 대기 없이 `--summary-only`로 텍스트부터 제공한다. 이 모드는 이미지나 카드 metadata를 읽지 않는다.
- 새 카드의 기본은 핵심 2장이며 사용자 지정 장수가 우선한다. 다른 주제는 텍스트 요약에 포함한다. 내장 image_gen이 있으면 병렬 생성하고, 같은 이미지의 반복 생성은 피한다.
- Black Sheep Wall을 사용하는 경우 설치된 원본 스킬의 카드뉴스 지침을 읽는다. 선택적인 연결이며 이 저장소는 해당 스킬이나 외부 이미지 서비스를 설치하지 않는다. 참고 원본: https://github.com/csm-kr/black-sheep-wall (검토 commit `a7c945c638780c95d82ab31d763f6711adc03f0e`).

## 지정한 열린 방의 안읽음 수집

기존 `collect.swift`는 스크롤 없는 정확한 창의 AX 행을 읽는다. 추가 `kakao_room_nav.swift`는 방 검색/열기/스크롤을 수행한다. 검색과 열기는 전경을 바꾸고, 방 열기는 읽음 상태를 바꿀 수 있다. 사용자 요청에 있는 방만 사용한다. native 실행기는 별도로 컴파일하고 bootstrap이 돌려준 collector 경로를 전달한다.

```sh
swiftc <skill>/scripts/kakao_room_nav.swift -o <project>/.tools/kakao-room-nav
# 사용자가 지정한 방 검색 → 결과의 정확한 제목과 행 번호 확인
<project>/.tools/kakao-room-nav search '<지정한 방 검색어>'
<project>/.tools/kakao-room-nav open '<확인한 정확한 제목>' '<확인한 행 번호>'
python3 <skill>/scripts/kakao_unread_capture.py \
  --title '<확인한 정확한 열린 방 제목>' \
  --collector '<bootstrap 출력의 collector 경로>' \
  --navigator '<project>/.tools/kakao-room-nav' \
  --output-dir '<project>/var/unread'
```

검색 결과가 여러 개면 임의로 고르지 않는다. native 출력에는 원문이나 제목이 포함될 수 있으므로 비공개 파일로 저장한다. navigator는 잠금 상태에서 앱 접근 전에 멈추고 메시지 전송은 구현하지 않는다.

수집기는 처음 현재 노출 행 최대200개를 읽고, 아래로 로딩한 뒤에는 증가한 행+겹침2행만 읽는다. 안읽음 구분선이 유일하지 않거나 행 번호/겹침이 바뀌거나 최하단을 확인하지 못하면 완료로 표시하지 않는다. 실행마다 별도 비공개 폴더를 만들고 부분 캡처를 남긴다. 세션 간 마지막 읽은 위치 추적·전체 과거 이력·작성자·메시지 날짜·미디어 해석은 지원하지 않는다.

## 텍스트/이미지 파일 계약

한 작업 폴더에 아래 파일을 준비한다. 실제 대화·생성 결과물은 공개 저장소에 넣지 않는다.

`digest.json`:

```json
{
  "title": "AI 소식과 대화 요약",
  "collected_date": "YYYY-MM-DD",
  "room": "사용자가 지정한 방",
  "scope": "실제로 수집한 범위와 한계",
  "overview": ["핵심 요약"],
  "sections": [{"title": "카톡 대화", "items": [{
    "title": "주제", "summary": "내용 요약", "useful": "활용 포인트",
    "status": "참여자 의견 · 미검증", "sources": []
  }]}]
}
```

`sources`는 `[{"label":"출처 이름","url":"https://..."}]` 형식이다. 원문이 공개되지 않은 대화의 URL은 만들어내지 않는다. 개인 이름·프로필·민감한 원문을 요약/이미지에 옮기지 않는다.

`copy.json`은 `{"cards":[...]}`이며 카드에는 `id`(파일명으로 쓸 영문·숫자·하이픈), `topic`, `headline`, `summary`, `card_summary`, `generation_prompt`, `labels`, `badge`, `short_limit`, `bullets`, `why`, `caution`, `sources`를 넣는다. `generation_prompt`와 이미지 안 문구를 일치시킨 뒤 이미지를 생성한다.

생성 후 `generated-sources.json`은 다음 계약을 쓴다. 키는 실제 생성에 사용한 카드에서 계산한다. 문구가 바뀐 뒤 기존 이미지의 키를 새 값으로 바꿔 재사용 판정을 조작하지 않는다.

```python
# scripts/cardnews_gallery.py를 import한 뒤
record = {"backend": "built-in image_gen", "cards": [
    {"id": card["id"], "path": "/local/generated.png", "image_key": image_key(card)}
]}
```

```sh
python3 <skill>/scripts/cardnews_gallery.py --directory <output> --summary-only
python3 <skill>/scripts/cardnews_gallery.py --directory <output> --plan-images
# plan의 generate 카드만 생성하고 원본 경로·키를 기록한 뒤 조립
python3 <skill>/scripts/cardnews_gallery.py --directory <output>
# 단일 파일로 전달할 때만 이미지 내장
python3 <skill>/scripts/cardnews_gallery.py --directory <output> --embed-images
```

기본 `index.html`은 이미지와 분리된 가벼운 HTML이며 PNG를 지연 로딩한다. 폴더째 보관한다. `summary.html`/`summary.md`는 이미지 없이 읽을 수 있다. `images.json`은 PNG의 실제 치수·SHA-256을 기록한다. PNG는 수정하지 않고 복사하며 내용이 같으면 재복사하지 않는다. 목표 치수를 실제 생성 치수로 보고하지 않는다.

## 검증 범위

- KakaoTalk 26.9에서 지정한 커뮤니티 방 두 곳의 텍스트 읽기와 구분선부터 최하단 탐색을 실제 관찰했다. 원문과 방 이름은 배포하지 않는다.
- 최적화 wrapper는 모의 검사와 실제 저장 캡처 재생으로 검증했다. 한 재생 사례의 중복 포함 본문 검사 행은912→288로 줄고 최종250행은 같았다. 새 wrapper의 실앱 속도 향상이나 전체 요청 완료 시간은 실측하지 않았다.
- 내장 image_gen의 이미지 카드와 요약 조립을 실제 실행했다. 이미지 생성 자체는 여전히 별도의 시간이 걸린다. HTML 파일 크기 감소를 수집·요약·생성 전체 속도 향상으로 주장하지 않는다.
