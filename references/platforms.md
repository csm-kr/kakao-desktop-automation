# 플랫폼과 화면 전환

## macOS foreground

포함한 patch의 source pin은 kmsg `54cbff790b409214735ef365a621d791034fe500`이다. 원본 VERSION은 `1.260921.0`이다. 실제 앱 시험은 KakaoTalk 26.3에서 정확한 열린 대화창과 빈 초안을 확인하고, foreground AX 입력/Enter 한 번 뒤 신규 발신 메시지를 관찰하는 범위였다.

이 실행기는 앱 활성화·창 올리기·입력창 포커스·키 이벤트를 사용한다. 다른 앱에서 계속 입력하는 사용자와 동시에 실행하면 안 된다. 단순히 `activate()`를 제거해도 Enter의 전달 대상이 보장되는 것은 아니다.

## macOS background

패키지의 `background-experimental` variant는 AXSelectedText/AXPress 경로를 foreground와 분리한 코드다. activation·raise·focus·CGEvent fallback을 사용하지 않는 정책을 모의 driver로 검사했다. 포함한 background patch는 같은 고정 commit에 단독 적용하는 전체 patch다. foreground patch 위에 덧붙이지 않는다.

```sh
python3 <skill-directory>/scripts/bootstrap_kmsg.py --project <project-directory> --variant background-experimental
<experimental-kmsg-path> send '<exact-open-chat-title>' --background-safe --check-only
```

`--check-only`는 읽기만 한다. 버튼·입력창·동일 창의 준비 조건을 확인할 수 없으면 중단하고, 창을 복원하거나 입력을 넣지 않는다. commit 경로는 별도 사용자 발송 요청 후에만 사용한다. 단일 AXPress 반환은 전송 확인이 아니며 독립적인 신규 메시지 확인이 필요하다.

### 실제로 확인한 범위

KakaoTalk **26.9.0 build2006**에서 허가된 controlled test 한 건을 수행했다. 준비 때 유일한 창과 빈 초안을 확인한 뒤 다른 앱을 전경에 두고 본문을 한 번 삽입했다. 입력 직후 버튼이 아직 disabled여서 첫 sender는 실제 press 없이 멈췄다. 이후 이미 작성된 정확한 자체 초안을 보존하는 **별도 복구**에서 enabled 버튼을 한 번 눌렀다. press 반환은 AX 오류 `-25200`이었지만 재시도하지 않았고, 독립 관찰에서 새 발신 메시지 0→1과 빈 입력창을 확인했다.

삽입 구간과 복구 press 구간은 각각 NSWorkspace 알림과 약 10ms polling으로 전경 변화를 관측했으며 두 구간에서 관측 전환은 0회였다. 초기 창 열기·binding과 복구 준비의 전경 전환은 이 범위에서 제외했다. **준비부터 끝까지 전환 0회, 모든 상태에서의 성공 또는 공개 단일 sender 한 번 호출의 end-to-end 성공을 뜻하지 않는다.**

실험용 삽입·복구 harness와 실제 메시지·계정 기록은 패키지에 포함하지 않는다. 일반 sender는 기존 초안을 거부한다. controlled 복구를 임의 초안 resume 권한으로 확대하지 않는다. 최신 generic sender는 입력 후 최대 2초, 50ms 간격으로 읽기만 하며 버튼 활성화를 확인한다. 매 관찰에서 창·본문·control 조건을 다시 검사하고, 추가 삽입 없이 press는 최대 한 번이다. 지연 활성화와 timeout·초안/identity 변화의 중단은 모의 검사로 확인한 코드 개선이며, 이 generic sender의 실기 성공을 뜻하지 않는다.

### Retained-window 준비와 commit

다른 앱으로 전환한 뒤 새 프로세스로 `AXWindows`를 읽으면 창이 보이지 않을 수 있다. 이 경우에는 승인된 준비 구간에 유일한 exact 창을 먼저 검증하고 **같은 프로세스**에 보관하는 모드를 고려한다.

```sh
<experimental-kmsg-path> send '<exact-open-chat-title>' '<new-authorized-message>' --background-safe --wait-for-commit --commit-timeout 30
```

프로세스는 준비 결과 JSON의 nonce와 `commitSignal`을 stdout에 내보낸 뒤 stdin을 기다린다. 컨트롤러는 사용자가 이미 허가한 대상·본문과 준비 상태를 대조하고 필요한 전경 준비를 마친 뒤, 해당 실행이 반환한 **`COMMIT <nonce>` 한 줄**만 같은 프로세스 stdin에 전달한다. 예시 nonce를 하드코딩하거나 다른 실행의 receipt를 재사용하지 않는다. nonce는 실행 연결용이지 새 발송 허가가 아니다.

준비 실패·timeout·잘못된 신호는 입력 전에 멈춘다. commit 뒤 불명확하거나 생성한 초안이 남으면 자동 재실행·본문 재삽입·범용 resume를 하지 않는다. press 반환과 종료 코드만으로 성공 처리하지 않는다.

공개 `probe`는 **AXWindows만으로 exact 창을 찾는다**. 창 열거가 0이면 차단/확인 미완료다. controlled test의 별도 same-PID focused/main hint 관찰을 이 generic probe의 검증으로 취급하면 안 된다. 이 probe만으로 background sender의 완료까지 자동 판정하는 통합은 아직 검증되지 않았다.

참고한 OpenKakao의 [AX sender 원본](https://github.com/JungHoonGhae/openkakao-cli/blob/78c2c16b903ae76d5bbf9dcbf80f806dbf8e3712/src/ax_send.rs)은 `AXSelectedText`와 실제 전송 버튼 `AXPress` 경로를 제공하며 제작자 검증 주석은 KakaoTalk 26.9/macOS 26을 명시한다. 이는 26.3에서의 성공 근거가 아니다.

추가 도입 시 exact 열린 창·빈 입력창·settable 속성·유일한 전송 버튼을 확인하고, 실제 controlled test에서 전경 앱 변화와 키 이벤트 0회, 신규 발신 메시지를 각각 검증한다. 버튼은 빈 초안일 때 disabled일 수 있으므로 준비 검사와 입력 후 enabled 확인을 구분한다. 불가능하면 foreground로 자동 fallback하지 않는다.

## Windows

Swift의 AppKit/ApplicationServices를 사용하는 이 실행기는 Windows 지원이 아니다. 다음은 별도 어댑터를 조사할 원본 후보다.

- [pyautokakao](https://github.com/DDadeA/pyautokakao): Win32 HWND, text 설정과 키 메시지, 별도 읽기 경로
- [KakaoTalk-python](https://github.com/Xenia101/KakaoTalk-python): Windows 창을 찾아 text와 Enter 메시지를 전달하는 예제
- [pywinauto](https://github.com/pywinauto/pywinauto): UIA/Win32 제어를 위한 범용 기반

후보 저장소의 존재는 최신 카카오톡 호환성이나 비전경/잠금/RDP 동작의 검증이 아니다. 실제 Windows 버전과 카카오톡의 control tree, 입력·commit 수용, 신규 발신 결과, 전경 변화 여부를 별도 시험해야 한다. Win32의 window message는 카카오톡 서버 메시지 API와 다르다.
