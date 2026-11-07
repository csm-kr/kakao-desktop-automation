# 단일 전송의 확인

## 원칙

원본 kmsg의 `send --dry-run`은 앱에 접근하지 않는다. 인자 확인에만 쓰고 대화창·대상 확인을 대신하지 않는다. 이 패키지의 patched sender도 전송 확인을 대신하지 않는다.

한 작업의 수신자, 문구, 요청 ID, 전송 전 상태를 로컬 기록으로 남긴다. 요청 파일은 배타 생성으로 같은 작업의 중복 실행을 막고, 다른 에이전트가 같은 요청을 보내지 않도록 단독 담당을 둔다. 전송 후 결과를 기록하기 전에 프로세스가 종료된 경우는 자동 재전송할 수 있는 실패로 취급하지 않는다.

## Read-only probe

bootstrap 결과의 `probe`는 특정 열린 대화창만 읽는다. 활성화·포커스·검색·텍스트 설정·키 입력·발송을 하지 않는다.

```sh
<probe-path> '<exact-open-chat-title>' '<message-to-check>' > before.json
```

`ready=true`, `exact_window_count=1`, 유일한 빈 입력창, 메시지 목록을 읽었는지 확인한다. 해당 창이 AX에 노출되지 않으면 probe는 오류를 반환하며 창을 복원하지 않는다. 이 상태를 낮은 latency의 성공으로 보고하면 안 된다.

명시적인 화면 잠금은 `session_screen_locked`, 로그인 미완료/console 비활성은 `session_not_interactive`로 앱 AX 조회 전에 멈춘다. `session_flags`에는 읽은 값만 보존한다. 이전 unlocked 관찰에서는 잠금 키 자체가 없었으므로, 키 누락을 명시적 false나 무조건 잠금 해제라고 표현하지 않는다. 전후 비교도 명시적 잠금·비대화형 상태 또는 잘못된 session 값이 포함된 기록을 거부한다. 잠금을 우회하거나 로그인을 대신 수행하지 않는다.

이 공개 probe는 `AXWindows`만 사용한다. background 상태에서 그 배열이 0이면 실제 창의 존재나 발송 결과를 확인하지 못한다. controlled test에 쓰인 별도 focused/main hint observer나 복구 harness는 포함하지 않으며, 그 실험 성공을 이 probe 또는 단일 호출 sender의 통합 성공으로 옮겨 보고하지 않는다.

사용자에게 허가받은 한 번의 sender 호출 뒤 같은 probe로 `after.json`을 만든다. 본문 대신 SHA-256과 동일 문구의 개수만 출력한다.

```sh
<probe-path> '<exact-open-chat-title>' '<message-to-check>' > after.json
python3 <skill-directory>/scripts/verify_delivery.py before.json after.json
```

비교 도구는 같은 PID·제목·문구 hash·검사 범위에서 발신으로 분류한 동일 문구가 정확히 하나 늘고 입력창이 빈 경우만 `confirmed_in_ui`로 반환한다. 메시지 배열 tail이 바뀌어 같은 과거 문구가 범위에서 빠지면 확인은 보수적으로 실패할 수 있다. 이때 자동 재전송하지 않는다.

발신 분류는 실제 AX text 위치가 메시지 table의 오른쪽에 있는지 보는 휴리스틱이다. 새 앱 레이아웃에서는 별도 검증해야 한다. 입력창 비움과 새 UI 메시지는 서버 message_id·최종 전달·상대방 읽음의 증거가 아니다.

## 불명확한 결과

- commit 전 확실한 거부: 대상 창이 없거나 중복, 기존 초안, 입력창 미확인. 실제 입력/Enter가 없음을 확인한 뒤 준비 상태를 고칠 수 있다.
- commit 후 불명확: timeout, sender 종료, probe 누락, UI 변화가 증명되지 않음. 기록을 `unconfirmed`로 남기고 같은 문구를 자동으로 보내지 않는다.
- 확인된 결과: `confirmed_in_ui`와 전후 근거를 연결한다. 이미 완료한 작업은 재실행하지 않는다.

예약은 이 상태 구분을 구현한 별도 sender 계약을 연결한 후에만 활성화한다. 확인되지 않은 native message_id를 임의 문자열로 만들어 계약을 통과시키지 않는다.
