# 공개 후보 검증 기록

이 문서는 기능별 검증 기록이다. 초기 후보 검사와 후속 업데이트를 구분하며, 과거 단계의 미검증 기록을 최신 검증으로 확대하지 않는다. 원격 push는 테스트나 실제 앱 검증을 대신하지 않는다.

## 기능 근거와 한계

- **Foreground:** KakaoTalk 26.3에서 개인 대화 두 건의 새 발신 메시지와 빈 입력창을 실제로 확인했다. 실명·별칭·본문·실행 로그는 이 후보에 포함하지 않는다.
- **Background experimental:** KakaoTalk 26.9.0 build2006에서 controlled 2단계 한 건(삽입 1회, 별도 복구 press 1회)을 확인했다. 두 관측 구간의 전경 전환은 0회였지만 준비 전환은 제외했다. press가 -25200을 반환해도 재시도하지 않고 독립 관찰로 새 발신 메시지와 빈 입력창을 확인했다. 공개 generic 단일 호출 sender의 end-to-end 성공은 아직 검증하지 않았다.
- **Windows:** 이 패키지는 실행을 지원하지 않는다. Win32/UIA 원본 후보를 참고하는 문서만 포함한다.
- **Generic probe:** source가 native compile되고 `--help`가 실행됐다. 메시지 내용을 출력하지 않으며 실제 앱에서의 모든 레이아웃을 검증한 도구는 아니다. 전후 비교는 UI 관찰일 뿐 서버 message_id나 상대방 읽음 확인이 아니다.

## 로컬 패키지 검사

| 검사 | 범위 | 상태 |
|---|---|---|
| 스킬 구조 | name/description·파일 구조 | quick_validate 통과 |
| Bootstrap fixture | 고정 Git commit, patch 적용, 캐시 재사용, 원본 변경 보존, 실패 정리, Windows 거부, symlink 경계 | 단위 검사 통과(현재 결과 비교와 합계 14개) |
| 결과 비교 fixture | 새 발신 메시지 정확히 +1, 동일 대상·본문·PID·범위, 초안·중복·누락 거부 | 단위 검사 통과 |
| Native probe | -O compile 및 도움말만 실행 | 통과, 앱 접근 없음 |
| 실제 고정 소스 bootstrap | 새 checkout → foreground patch → release build 및 probe compile → 재사용 | 통과. `built` 다음 `reused`, CLI VERSION `1.260921.0` 확인 |
| 고정 source patch 적용 | 깨끗한 같은 upstream에서 foreground/background 각각 단독 `git apply --check` | 두 variant 모두 통과 |
| 공개 파일 검사 | 개인 식별자·토큰·사용자 절대 경로·로그/DB/바이너리 배제, 문서 링크 | 게시 직전 tracked 파일과 새 소스를 검사 |
| JSON sender | 가짜 probe/kmsg, UI 기록 대조, 재호출·중단·원장 변경·동시 실행 | 14개 검사 통과(잠금 보호 포함) |
| 예약 CLI | 서울 시간·단일 claim·미확인 보류·UI 기록 대조·기존 DB upgrade | 13개 검사 통과 |
| subprocess 통합 | 예약→adapter→가짜 kmsg/probe→UI 기록→중복 차단 | 1개 검사, session guard 추가 후 공개 패키지 합계 41개 |
| session guard | 명시적 잠금 시 native probe의 AX 조회 전 중단, sender 호출0, 전후 확인 거부 | 회귀 2개 추가로 합계41개. 실제 잠금 상태의 native probe 차단도 확인 |
| 정보 수집·선별 | exact 방/행 범위·키워드·링크·로컬 재선별·원문 출처/행 번호·데이터 지시 미실행 | 모의9개와 collector cache 회귀1개 추가, 합계51개 |
| 현재 세 helper bootstrap | 고정 원본 background patch release build, probe/collector compile, 실제 재사용·version/help | 통과. 실제 앱 수집·발송은 미포함 |

실제 bootstrap 검사에서 원본 저장소의 내부 문서 symlink를 일괄 거부하는 문제가 발견돼 수정했다. checkout 내부의 파일을 가리키는 symlink만 hash에 포함하고, 외부를 가리키는 symlink와 사용자 수정은 거부한다. fixture에도 이 경계를 추가했다.

JSON sender는 독립 에이전트가 가짜 실행 파일로도 검수했다. state_dir 변경으로 원장을 우회하는 문제와 부모 중단 뒤 남은 자식에 다른 요청이 겹치는 문제를 재현해 수정했다. 고정 registry anchor·원장 lock·미완료 호출 보류를 넣었고, 서로 다른 설정 디렉터리가 같은 원장을 사용하는 동시성도 검사했다. 실제 앱/UI·추가 전송·OS 타이머 활성화는 이 검수에 포함하지 않았다. 공개 adapter의 한 번 호출 실기 검증은 여전히 남아 있다.

session guard는 새 native probe를 컴파일하고 실제 잠긴 Mac에서 `session_screen_locked`와 `ax_observation_attempted=false`를 확인했다. 이 읽기 시험은 잠금 해제·발송이나 unlocked 상태에서의 새 end-to-end 성공이 아니다. probe source hash가 달라졌으므로 기존 bootstrap receipt를 임의로 고치거나 binary를 덮지 않는다. bootstrap이 이전 버전을 거부하면 기존 설치를 보존하고 새 관리 디렉터리에서 빌드한다.

정보 수집은 별도 native collector로 구현했다. 독립 fake 검수에서 불가능한 원본 행 번호를 받아들이는 문제를 재현·수정했고 exact tail 개수/순서로 검증한다. 원문과 적용 규칙·capture/row 출처는 로컬에 저장한다. 작성자/날짜/전체 이력/의미 요약은 검증한 기능이 아니다. 실제 잠금 상태에서 collector가 AX 조회 전에 멈춘 결과만 실측했으며 실제 방 수집은 하지 않았다.

최신 background patch의 release build와 정책 검사 32개는 별도 개발 작업에서 통과한 결과다. 입력 후 2초/50ms 읽기 polling과 매번의 상태 재검사, timeout·본문/identity 변경 중단을 포함한다. 이 코드는 controlled test의 별도 복구를 통합하려는 후속 개선이며 실제 단일 호출 end-to-end 검증을 대신하지 않는다. 이 패키지 bootstrap의 새 checkout 전체 빌드는 foreground variant로 실행했다. generic probe는 새로 일반화한 도구로 compile/도움말만 확인했다. 특히 background에서 AXWindows가 0이면 공개 probe는 차단되므로 private hint observer로 확인한 controlled 결과를 공개 probe의 성공으로 옮기지 않는다.

## 고정된 출처

- kmsg upstream: `54cbff790b409214735ef365a621d791034fe500`
- foreground patch SHA-256: `22cd01143348b760355709674c45dcb638ab2ab76e83a215d8198367e1c847c0`
- experimental background patch SHA-256: `f774376b933c3637fb07e0876dcce2fa4145c29c26c1803d947ddea036c6d75d`

두 patch는 같은 upstream commit에 각각 단독 적용한다. 순서대로 덧붙이지 않는다. source 변경 시 receipt와 기존 설치를 자동으로 덮지 않고 새 검증을 요구한다.

## 게시 전 확인

### 빠른 요약·카드뉴스 후속 업데이트

- KakaoTalk 26.9의 지정한 커뮤니티 방 두 곳에서 native collector의 텍스트 읽기와 안읽음 구분선부터 최하단까지의 탐색을 실제 관찰했다. 원문·방 이름·작성자·생성 이미지·절대 경로는 배포하지 않는다. 작성자/날짜/미디어 해석을 추가로 검증했다는 뜻은 아니다.
- 에이전트가 AI 소식과 대화를 요약하고 내장 image_gen으로 카드를 생성한 뒤 HTML/Markdown을 조립했다. Python 자체의 모델 자동 호출이나 정기 실행은 아니다.
- 최적화 수집 wrapper는 증가분+겹침2행을 읽는다. 실제 저장 캡처 재생에서 최종250행은 같고 중복 포함 본문 검사는912→288행이었다. 실제 wrapper의 새 앱 수집 속도와 전체 요청 latency는 측정하지 않았다.
- 공개 모의 검사는 **62개 통과**다. 기존51개에 안읽음 수집4개와 카드 조립/캐시7개를 더했다. 경계 없음·행 이동·최하단 미확인 중단, 이미지 없는 텍스트 조립, 변경 카드 생성 계획, stale 이미지 거부, 지연 로딩·동일 PNG 복사 생략·단일 파일 내장을 검사한다.
- navigator는 native compile과 앱에 접근하지 않는 `--help`가 통과했다. 방 검색/열기는 전경을 바꿀 수 있으며 메시지 전송은 구현하지 않는다. 최적화 wrapper가 모든 방이나 레이아웃에서 성공한다고 주장하지 않는다.
- 스킬 구조 검사와 공개 파일 점검을 통과한 파일만 반영한다. `output/`, `generated_images/`, 원문 캡처와 바이너리는 배포 대상에서 제외한다.

실제 background 결과는 위의 제한된 범위로만 반영했다. 아직 통과하지 않은 기능을 출시 문구에 포함하지 않는다. 게시 담당자가 최종 파일 목록·검증 상태를 확인한 후 사용자의 조건부 공개 요청이 충족됐는지 판단한다. 이 후보에는 credentials, 실제 수신자 목록, 실행 binary, 개인 대화 자료를 추가하지 않는다.
