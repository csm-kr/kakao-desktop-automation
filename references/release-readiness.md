# 공개 후보 검증 기록

이 문서는 로컬 패키지 준비 상태다. 원격 저장소 생성·push·릴리스 또는 실제 새 메시지 전송은 이 패키지 준비 작업에서 수행하지 않았다.

## 기능 근거와 한계

- **Foreground:** KakaoTalk 26.3에서 개인 대화 두 건의 새 발신 메시지와 빈 입력창을 실제로 확인했다. 실명·별칭·본문·실행 로그는 이 후보에 포함하지 않는다.
- **Background experimental:** KakaoTalk 26.9.0 build2006에서 controlled 2단계 한 건(삽입 1회, 별도 복구 press 1회)을 확인했다. 두 관측 구간의 전경 전환은 0회였지만 준비 전환은 제외했다. press가 -25200을 반환해도 재시도하지 않고 독립 관찰로 새 발신 메시지와 빈 입력창을 확인했다. 공개 generic 단일 호출 sender의 end-to-end 성공은 아직 검증하지 않았다.
- **Windows:** 이 패키지는 실행을 지원하지 않는다. Win32/UIA 원본 후보를 참고하는 문서만 포함한다.
- **Generic probe:** source가 native compile되고 `--help`가 실행됐다. 메시지 내용을 출력하지 않으며 실제 앱에서의 모든 레이아웃을 검증한 도구는 아니다. 전후 비교는 UI 관찰일 뿐 서버 message_id나 상대방 읽음 확인이 아니다.

## 로컬 패키지 검사

| 검사 | 범위 | 상태 |
|---|---|---|
| 스킬 구조 | name/description·파일 구조 | quick_validate 통과 |
| Bootstrap fixture | 고정 Git commit, patch 적용, 캐시 재사용, 원본 변경 보존, 실패 정리, Windows 거부, symlink 경계 | 단위 검사 통과(결과 비교와 합계 12개) |
| 결과 비교 fixture | 새 발신 메시지 정확히 +1, 동일 대상·본문·PID·범위, 초안·중복·누락 거부 | 단위 검사 통과 |
| Native probe | -O compile 및 도움말만 실행 | 통과, 앱 접근 없음 |
| 실제 고정 소스 bootstrap | 새 checkout → foreground patch → release build 및 probe compile → 재사용 | 통과. `built` 다음 `reused`, CLI VERSION `1.260921.0` 확인 |
| 고정 source patch 적용 | 깨끗한 같은 upstream에서 foreground/background 각각 단독 `git apply --check` | 두 variant 모두 통과 |
| 공개 파일 검사 | 개인 식별자·토큰·사용자 절대 경로·로그/DB/바이너리 배제, 문서 링크 | 최종 16개 파일 검사 통과, 일치·누락 0개 |

실제 bootstrap 검사에서 원본 저장소의 내부 문서 symlink를 일괄 거부하는 문제가 발견돼 수정했다. checkout 내부의 파일을 가리키는 symlink만 hash에 포함하고, 외부를 가리키는 symlink와 사용자 수정은 거부한다. fixture에도 이 경계를 추가했다.

최신 background patch의 release build와 정책 검사 32개는 별도 개발 작업에서 통과한 결과다. 입력 후 2초/50ms 읽기 polling과 매번의 상태 재검사, timeout·본문/identity 변경 중단을 포함한다. 이 코드는 controlled test의 별도 복구를 통합하려는 후속 개선이며 실제 단일 호출 end-to-end 검증을 대신하지 않는다. 이 패키지 bootstrap의 새 checkout 전체 빌드는 foreground variant로 실행했다. generic probe는 새로 일반화한 도구로 compile/도움말만 확인했다. 특히 background에서 AXWindows가 0이면 공개 probe는 차단되므로 private hint observer로 확인한 controlled 결과를 공개 probe의 성공으로 옮기지 않는다.

## 고정된 출처

- kmsg upstream: `54cbff790b409214735ef365a621d791034fe500`
- foreground patch SHA-256: `22cd01143348b760355709674c45dcb638ab2ab76e83a215d8198367e1c847c0`
- experimental background patch SHA-256: `f774376b933c3637fb07e0876dcce2fa4145c29c26c1803d947ddea036c6d75d`

두 patch는 같은 upstream commit에 각각 단독 적용한다. 순서대로 덧붙이지 않는다. source 변경 시 receipt와 기존 설치를 자동으로 덮지 않고 새 검증을 요구한다.

## 게시 전 확인

실제 background 결과는 위의 제한된 범위로만 반영했다. 아직 통과하지 않은 기능을 출시 문구에 포함하지 않는다. 게시 담당자가 최종 파일 목록·검증 상태를 확인한 후 사용자의 조건부 공개 요청이 충족됐는지 판단한다. 이 후보에는 credentials, 실제 수신자 목록, 실행 binary, 개인 대화 자료를 추가하지 않는다.
