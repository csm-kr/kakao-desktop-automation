# Kakao Desktop Automation

Codex 등 에이전트가 로그인된 macOS 카카오톡에서 **대상 확인 → 빈 초안 확인 → 한 번의 전송 → 독립 결과 확인**을 수행하도록 만든 프로젝트용 스킬이다.

실제 확인한 기능과 실험 중인 기능을 구분해 배포한다. 아래 검증 범위가 현재 지원 판정이다.

## 포함한 범위

- `SKILL.md`: 에이전트 실행 절차
- 고정된 [kmsg](https://github.com/channprj/kmsg) commit과 foreground·experimental background patch
- 프로젝트 로컬 소스 빌드·캐시 재사용 스크립트
- 메시지 내용을 출력하지 않는 read-only AX probe와 전후 결과 비교 도구
- 백그라운드/Windows 지원 상태와 검증 방법
- SQLite 일회·매일 예약 CLI와 요청/확인 기록을 잇는 JSON sender
- 지정한 방의 AX 텍스트와 키워드·링크 조건을 출처에 연결하는 로컬 선별 도구

| 항목 | 검증 상태 |
|---|---|
| macOS foreground 송신 | KakaoTalk 26.3에서 개인 대화 두 건의 신규 발신 메시지와 빈 입력창 확인 |
| 포커스 없는 background 송신 | KakaoTalk 26.9.0 build2006에서 controlled 2단계 한 건 확인. 삽입/commit 관측 구간의 전경 전환 0회, 준비 전환 제외. 공개 단일 호출 sender의 end-to-end 성공은 미검증 |
| Windows 실행 | 미지원. 별도 UIA/Win32 어댑터 조사 필요 |
| 상대방 읽음·서버 message_id | 확인하지 않음 |
| 예약·무인 운영 | 타이머 미포함. 새 조건과 실제 sender 검증 필요 |
| JSON sender adapter | 요청 원장·background 호출·UI 확인 기록 연결. 가짜 backend로 검증, adapter 실기 미검증 |
| 정보 수집·선별 | exact 방·노출된 AX 행·명시한 키워드/링크 조건. 가짜 캡처로 검증, 실제 방 수집 미검증 |

실제 계정명, 대화 이름, 테스트 본문, 로그, 대화 DB, 바이너리는 포함하지 않는다. 이름과 본문은 실행할 때 사용자가 지정한다.

## 프로젝트에 스킬 연결

이 폴더를 `<project>/.agents/skills/kakao-desktop-automation/`에 배치한다. 전역 설치는 필요하지 않다. 에이전트에게 `$kakao-desktop-automation`으로 요청하거나 스킬 설명에 맞는 작업을 지시한다.

Mac의 Python 3.10+, Git, Swift 6/Xcode Command Line Tools가 필요하다. bootstrap은 새 도구 체인이나 권한을 설치하지 않는다.

```sh
python3 scripts/bootstrap_kmsg.py --project /path/to/project
```

출력 JSON에 나오는 `kmsg`와 `probe`가 로컬 실행 파일이다. 재실행하면 검증한 checkout/patch/바이너리를 재사용한다. 준비 단계는 실제 앱에 접근하지 않는다. 실제 전송은 [SKILL.md](SKILL.md)의 대상·초안·요청 기록 확인 후에만 실행한다.

추가 `collector`는 [정보 수집·선별](references/insights.md)의 read-only 경로에서 사용한다. 원문은 로컬 비공개 파일로 저장하며 작성자·날짜·전체 이력을 보장하지 않는다.

experimental 배경 경로는 `--variant background-experimental`을 붙여 별도 checkout으로 만든다. 먼저 `kmsg send '<exact-open-chat-title>' --background-safe --check-only`로 읽기 전용 준비 검사를 한다. 이 검사의 통과는 실제 전송이나 화면 변화 0회의 증거가 아니다. [플랫폼 조건](references/platforms.md)을 따른다.

예약기에서 호출할 JSON 입출력 adapter와 중단·중복 기록은 [sender 연결](references/sender.md)을 따른다. 실제 카카오톡에서 adapter 검증 전에는 검증된 예약 sender로 등록하지 않는다.

일회·매일 예약은 [예약 CLI](references/scheduling.md)의 add/list/approve/cancel/run-due를 사용한다. 기본 DB는 명령을 실행하는 프로젝트의 `var/kakao-schedule.sqlite3`다. 예약 생성만으로 전송하지 않으며 OS 타이머는 자동 설치하지 않는다.

별도의 준비 단계에서 유일하게 열거된 창을 유지하는 retained 모드도 포함한다. 준비 자체는 화면을 바꿀 수 있다. 공개 probe는 `AXWindows`에 창이 노출되지 않으면 확인을 완료하지 못하며, controlled test에서 사용한 별도 복구 harness나 hint 경로를 포함하지 않는다.

## 검증과 배포

```sh
python3 -m unittest discover -s tests -v
```

테스트는 임시 Git 저장소·가짜 빌드와 JSON fixture를 사용한다. 카카오톡을 실행하거나 보내지 않는다. [공개 준비 상태](references/release-readiness.md)에 패키지 검사 결과와 남은 검증 조건을 기록한다.

새 버전을 낼 때는 고정 commit과 patch hash를 함께 갱신하고, 모의 테스트와 실제 송신 검증을 구분한다. foreground 성공을 background 또는 Windows 성공으로 확대하지 않는다.

프로젝트 코드는 [MIT](LICENSE), kmsg 부분은 [원본 라이선스](patches/kmsg-LICENSE)를 따른다. [출처](THIRD_PARTY.md)를 유지한다.
