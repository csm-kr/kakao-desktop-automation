# 카카오톡 예약 발송 준비

예약은 표준 Python CLI인 `scripts/kakao_schedule.py`가 관리한다. `scripts/kakao_sender.py`가 프로젝트 스킬의 background JSON sender에 연결되며 가짜 backend에서 예약→발송기→UI 확인 기록까지 통과했다. **이 통합 adapter의 실제 카카오톡 발송은 아직 검증되지 않았다.** 현재 활성 예약, cron, launchd는 없다. 기존 세 테스트는 이 스케줄러가 발송한 적 없으며 재전송하지 않는다.

## 예약 만들기와 확인

아래는 사용법 예시이며 실제 등록한 작업이 아니다. 먼저 실제 수신자를 식별할 수 있는 sender 전용 ID와 보낼 문구·일정을 정한다. 표시 이름만으로 대상을 추측하지 않는 것은 sender의 책임이다.

```sh
python3 scripts/kakao_schedule.py add --recipient self --message '오늘 일정 요약' --daily 09:00
python3 scripts/kakao_schedule.py add --recipient self --message '예약 테스트' --at '2030-01-01T09:00:00+09:00'
python3 scripts/kakao_schedule.py list
python3 scripts/kakao_schedule.py run-due
```

시간대는 `Asia/Seoul`이다. `--at`의 오프셋을 생략하면 서울 시각으로 해석하며 미래 시각만 받는다. 매일 예약은 다음 미래 시각을 첫 실행으로 잡는다. `--db /absolute/path/test.sqlite3`를 명령 앞에 붙이면 격리된 저장소를 쓴다. 기본 저장소는 프로젝트 `var/kakao-schedule.sqlite3`이며 수신자·본문이 평문으로 저장되므로 공유하지 않는다.

새 작업은 승인되지 않은 상태로 만들어진다. 수신자·본문·일정을 확인하고 사용자 발송 승인이 있는 작업에만 `approve JOB_ID`를 적용한다. `run-due`는 기본 dry-run으로 내용을 보여주고 발송·claim·작업 소모를 하지 않는다.

```sh
python3 scripts/kakao_schedule.py approve JOB_ID
python3 scripts/kakao_schedule.py cancel JOB_ID
python3 scripts/kakao_schedule.py attempts
```

취소는 다음 실행을 막는다. 이미 sender 실행이 시작된 발송을 되돌리지는 않는다.

## Sender 연결 계약

실제 sender의 통합 검증 후에만 활성화한다. 앱 내부 API와 macOS 접근성 API를 구분하며 현재 adapter는 후자다. 다음 설정 파일은 예시이며 `verified` 표시는 실제 검증을 대신하지 않는다. `verification_evidence`에는 대상 식별·본문·전송 결과를 확인한 실제 검증 기록의 로컬 경로를 넣는다. CLI는 설정 형식과 그 파일의 존재를 확인할 뿐, 카카오톡 서버와 통신하여 sender를 인증하지 않는다.

```json
{
  "command": ["/absolute/path/python3", "/absolute/path/project/scripts/kakao_sender.py", "--config", "/absolute/path/project/var/adapter.json"],
  "verified": false,
  "verification_evidence": "/absolute/path/sender-verification.md"
}
```

```sh
python3 scripts/kakao_schedule.py validate-sender /absolute/path/sender.json
python3 scripts/kakao_schedule.py run-due --execute --sender-config /absolute/path/sender.json
```

실행 파일은 절대 경로여야 한다. shell을 거치지 않고 argv 배열로 실행한다. sender는 stdin JSON 하나를 받고 stdout에는 JSON 하나만 반환한다. 진단 로그는 stderr에 쓴다. 요청 형식:

```json
{"version":1,"request_id":"JOB_ID:DUE_EPOCH","job_id":"JOB_ID","recipient":"self","message":"본문","scheduled_at":"2030-01-01T09:00:00+09:00","timezone":"Asia/Seoul"}
```

확인된 전송 성공은 종료 코드 0, 정확히 같은 `request_id`, `status: "sent"`와 다음 중 하나가 필요하다. 기존 API sender의 실제 `message_id`, 또는 `verification`의 `type: "ui_receipt"`·절대 파일 경로·SHA-256이다. UI 경로는 파일 hash, 요청 ID·수신자·본문 hash·exact title, 전후 동일 PID와 새 발신 정확히 +1·빈 초안, 호출/commit 각 1회를 다시 검사한다. AX 경로의 message_id는 null로 보존하며 임의 ID를 만들지 않는다.

전송 전에 막힌 sender는 `status: "not_sent"`와 `reason`을 반환한다. 입력 뒤 commit 0회로 멈춰 초안이 남았을 수도 있으므로 초안을 지우거나 재호출하지 않는다. 시간 초과·불완전 응답·다른 request ID·비정상 종료·불일치/변조된 UI 기록은 `unknown`이다.

스케줄러는 이 JSON 계약을 충족한 sender의 `sent` 응답을 신뢰한다. 카카오톡 수신 여부를 독립적으로 확인하지 않으므로, sender 자체가 실제 전송 결과를 확인한다는 검증이 먼저 필요하다.

```json
{"request_id":"JOB_ID:DUE_EPOCH","status":"sent","message_id":"ACTUAL_CONFIRMED_MESSAGE_ID"}
```

sender가 없거나 설정이 유효하지 않으면 승인된 due job도 `blocked`로 남고 소모되지 않는다. 발송 시작 전 SQLite `BEGIN IMMEDIATE`와 `(job_id, due_at)` 고유키로 한 실행만 claim한다. claim한 작업은 즉시 `held`가 되어 충돌 실행이나 프로세스 중단 후 자동 재전송을 막는다. 성공하면 1회 예약은 완료되고, 매일 예약은 다음 미래 시각으로 이동한다. Mac이 오래 꺼져 있던 경우 빠진 날짜만큼 몰아서 보내지 않는다.

`unknown`, `not_sent`, 중단으로 남은 `in_flight`는 자동 재시도하지 않는다. `attempts`와 실제 수신 결과를 사람이 대조한 후 후속 일정을 새로 정한다. 이 설계는 로컬 중복 시도를 제한하지만 카카오톡 서버의 exactly-once 전달을 보증하지 않는다.

## 실행 주기와 검증 범위

이 CLI는 항상 켜져 있는 데몬이 아니다. 실제 sender 및 사용자 일정이 확정되면 launchd 등이 `run-due --execute ...`를 주기적으로 호출하도록 연결한다. 이 작업에서는 OS 스케줄러를 활성화하지 않았다. Mac이 꺼지거나 절전 중이면 그 시각 실행을 보장할 수 없다.

adapter 설정·기록의 형식과 background 창 노출 제약은 [sender 연결](sender.md)을 따른다. 요청 원장과 설정 디렉터리를 유지해야 하며 기존 state_dir 값을 바꿔 미확인 호출을 무시하지 않는다. 부모가 중단된 intent가 있으면 자식 종료와 실제 UI 결과를 대조할 때까지 다른 요청도 막는다.

현재 스케줄러 검사는 13개이며 UI 확인 기록의 hash/요청/수신자/본문·관찰 일치와 기존 DB의 보존 upgrade를 포함한다. 별도 실제 subprocess 통합 검사는 예약기→JSON adapter→가짜 kmsg/probe→receipt 저장→중복 방지를 확인한다. 공개 패키지는 bootstrap/결과 비교 13개·sender 14개·예약기 13개·subprocess 통합 1개, 합계 41개 검사를 포함한다. 모두 fake backend 검사이며 실제 카카오톡 발송 성공 건수는 늘리지 않았다.

`python3 -m unittest discover -s tests -p 'test_kakao_schedule.py' -v`는 임시 DB와 가짜 sender만 쓴다. 서울 시간 해석, 승인·sender 누락 시 보존, dry-run, 동시 두 프로세스의 단일 claim, 중단·시간 초과·불명 응답의 재시도 차단, 매일 예약의 다음 시각을 확인한다. **모의 검증 통과는 카카오톡 전송 성공을 의미하지 않는다.**
