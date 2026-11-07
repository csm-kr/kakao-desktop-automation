# JSON sender와 UI 확인 기록

`scripts/kakao_sender.py --config /absolute/path/adapter.json`은 stdin 요청 하나를 받아 background kmsg를 최대 한 번 호출하고 stdout JSON 하나를 반환한다. `scripts/ui_receipt.py`는 요청과 전후 관찰을 대조한다. 이 연결부는 가짜 실행 파일을 사용한 계약 검사까지 완료했으며 **이 adapter의 실제 카카오톡 단일 호출 검증은 아직 없다.** 앞선 controlled 실험을 이 adapter의 성공으로 대신하지 않는다.

## 설정과 호출

다음은 형식 예시다. 실제 상대·본문·예약을 등록하거나 스케줄러를 활성화하는 명령이 아니다. 설정과 상태는 사용자 프로젝트의 비공개 `var/` 아래에 두고, 모든 호출에서 같은 상태 저장소를 유지한다.

```json
{
  "mode": "background-safe",
  "kmsg": "/absolute/path/to/experimental-kmsg",
  "probe": "/absolute/path/to/kakao-probe",
  "state_dir": "/absolute/path/to/project/var/sender-state",
  "recipients": {
    "recipient-id": {
      "title": "Verified exact chat title",
      "identity_evidence": "/absolute/path/to/private/identity-evidence.json"
    }
  }
}
```

recipient ID는 미리 식별한 exact 대화창과 매핑한다. identity evidence는 이름만 보고 상대를 추측하지 않기 위한 로컬 기록이며, 파일 존재 검사 자체가 실제 상대 인증을 대신하지 않는다. kmsg와 probe는 고정 소스 bootstrap으로 만든 실행 파일을 쓴다. 이 adapter는 앱 활성화·검색·창 열기·foreground fallback을 하지 않는다.

```json
{"version":1,"request_id":"unique-request-id","recipient":"recipient-id","message":"explicitly requested message"}
```

입력 전에 read-only probe로 exact 창 한 개, 빈 초안, 문구 hash와 발신 개수를 확인한다. AXWindows가 노출되지 않으면 입력 전 `not_sent`로 멈춘다. 현재 공개 probe는 retained ref나 focused/main hint를 사용하지 않으므로, hidden-window 상황을 자동으로 해결하는 서비스가 아니다.

화면이 잠긴 경우에는 명시적 `session_screen_locked` 사유로 kmsg를 호출하기 전에 멈춘다. 이때 저장 ref의 STALE_REF나 창 목록 0만 보고 앱의 전송 기능이 영구적으로 없다고 결론 내리지 않는다. 잠금 해제 이후의 실제 앱 동작은 따로 확인해야 한다. 이미 남긴 not_sent 요청을 자동으로 다시 실행하지 않는다.

kmsg에는 `send --background-safe -- <title> <message>`를 argv로 전달한다. shell은 사용하지 않는다. kmsg가 commit 1회를 보고한 경우에만 읽기 전용 결과 확인을 진행한다. AXPress 오류나 kmsg의 비정상 종료도 전송 실패로 단정하지 않는다. 기본 확인 대기는 2초이며 개별 probe의 최대 3초 실행 시간이 더해질 수 있다.

## 결과 계약

| status | 의미 |
|---|---|
| `sent` | commit 1회 보고와 독립 probe의 새 발신 +1·빈 초안 확인. 서버 ID/상대 읽음은 아님 |
| `not_sent` | 이번 요청이 전송 전에 막혔거나 kmsg가 commit 0회로 중단. 입력 후 남은 초안이 있을 수 있음 |
| `unknown` | commit·결과·기록을 확정할 수 없음. 자동 재전송 금지 |

성공 응답의 `message_id`는 null이다. 대신 `verification`에 `type: ui_receipt`, 절대 파일 경로, 파일 SHA-256을 반환한다. 기록에는 request ID·recipient ID·본문 hash·exact title·전후 메타데이터·단일 호출/commit 수가 담긴다. 본문 원문은 저장하지 않지만 대화 제목은 개인정보일 수 있어 외부에 게시하지 않는다.

예약기는 파일 hash, 요청 ID·상대·본문 hash·관찰 대상의 일치와 신규 발신 증가를 다시 검사한 뒤 UI 확인 근거를 저장할 수 있다. 이 파일은 로컬 관찰 근거이며, 디지털 서명된 서버 영수증이나 신뢰할 수 없는 sender에 대한 인증 수단은 아니다.

## 중복과 중단

- 설정 디렉터리의 고정 lock과 상태 저장소의 lock으로 호출을 직렬화한다. 서로 다른 설정 디렉터리도 같은 원장을 쓰면 같은 lock을 공유한다. `.kakao-sender-state.json`으로 최초 state_dir를 고정해 설정 변경만으로 빈 원장에 우회하지 않는다. 기존 설정 디렉터리와 원장을 함께 보존한다. 다른 위치로 새 설치하거나 원장을 삭제한 경우까지 전역 중복 방지를 보장하지 않는다.
- 요청별 intent는 배타 생성·fsync하고 실행 전에 in-flight를 기록한다. 같은 요청 ID와 본문은 완료 응답/기록만 재사용하며 새 프로세스를 시작하지 않는다. 내용이 달라지면 충돌로 거부한다.
- 부모 프로세스가 죽어 native 자식이 남을 수 있다. 단일 호출이 끝나지 않은 intent가 있으면 다른 요청 ID도 새 kmsg를 시작하지 않는다. 기존 자식의 종료와 실제 UI 결과를 대조해 기록을 정리해야 하며, 자동으로 intent나 초안을 지우지 않는다.
- 정상 반환된 unknown/not_sent도 같은 요청으로 자동 재실행하지 않는다. 프로세스 종료 코드만 보고 재시도하지 않는다. 같은 앱을 이 adapter 밖의 다른 도구가 동시에 제어하는 것은 이 lock으로 막을 수 없다.

권한·대상·새 본문·실제 실행 조건이 정해진 다음 한 번의 새 live 시험으로 확인해야 한다. 기존에 완료한 테스트를 재전송하지 않는다. 예약기를 연결할 때도 adapter 검증 전에는 `verified: true`로 설정하지 않는다.
