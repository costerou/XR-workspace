**Cody: this capture establishes a real software recovery loop before the hub disappears.** It makes a gentler driver worth testing. It does **not** establish that the loop causes the physical disconnect: the IMU stream already stops before the first reopen. The loop could be amplifying an existing glasses/firmware/link problem. Confidence is high about the observed sequence and the backoff weakness, moderate that reducing recovery traffic is a useful experiment, and low about the initiating root cause.

Nothing on the live system was changed. An offline, test-first backoff experiment has been committed separately on `drop-error-backoff`; details are below. No install or merge is authorized or performed.

**What was analyzed**

The two supplied folders overlap. I verified identical SHA-256 hashes for duplicate chunks and read each unique chunk once, in chronological order: **7 files,355,008 records,11:14:20.856–11:16:40.861 EST**. Every48-byte API0 header and captured-length boundary validates to EOF. This avoids double-counting the overlapping snapshots. No raw images, audio, keyboard contents, serial strings or IMU payloads are included in the derived artifact. USB setup headers and short known MCU commands/replies are retained. There is no capture-loss counter in these read() dumps.

The new installation directory is `bef4583dc3b7`, but its Luma driver SHA-256 is **identical** to the earlier `6744ab90ce82` build: `66028d8c3fb0faa37067d7a26d7222980a2ec173fb9a2a4426134bc33f915db6`. The renderer changed. Source comparison shows no driver-source change; tracking startup gained an isolation-readiness grace period, which does not alter this driver's USB transport.

**The actual sequence, including two corrections to the initial description**

1. XR runner starts at11:15:23.529813. At **23.588757–23.588791**, Luma address27 receives **eight interrupt-IN reads on0x81,1,024 bytes each**, followed by **eight bulk-IN reads on0x85,512 bytes each**. Those are requested buffer sizes, not actual IMU packet sizes.
2. At **23.608868**, one five-byte start command `fa55950000` is submitted to bulk-OUT0x04. It completes successfully at23.609012. This is the **only** command to the Luma device before the first whole-hub loss.
3. Successful28-byte IMU completions begin at23.610008. After290 successful completions, they stop at **23.836008**. The driver marks data stale at23.936 and its one-second watchdog tears down the connection around24.840.
4. The first visible `-EPROTO` is0x81 at **24.839803**, but **a cancellation completion already occurred at24.839658**. The log calls this episode `reason=silent`. Therefore that first EPROTO must not be presented as proof that a protocol error initiated the outage: it is interleaved with watchdog teardown. One canceled0x85 URB contains a six-byte95 reply; it was not delivered as a successful completion. The capture does not explain why it stayed pending.
5. Reads are queued again on the same device number at26.839704. Streaming resumes. The first unmistakable endpoint-halted completion,0x81 `-EPIPE`, is **28.458876**, followed by cancellation of the other reads. Thus the first halt is about4.87seconds after driver connection, not within its first second. The earlier failure was loss of successful IMU completions.
6. Two more0x81 halt episodes occur at38.827109 and43.690726. Between them, a second silent-watchdog teardown occurs around33.628, again with EPROTO among teardown completions. None of these is a USB re-enumeration: the Luma address remains27 and the parent hub remains24.
7. From **45.762870 through11:16:02.488200**, there are **nine**0x85 EPROTO episodes. Each follows a new queue of eight0x81 plus eight0x85 reads. Short bursts of successful IMU data occur on every reopen; the0x85 failure arrives roughly1.6–158ms after the bulk reads are submitted. These errors precede teardown in their episodes and are genuine recovery precursors, not merely the final hub-removal cancellation.
8. The last Luma cancellation completes at11:16:02.494214. **About0.814seconds later**, root-port status reports disappearance at **11:16:03.307992**, then the kernel logs the parent disconnect at03.308238. The returned status is again `00010100`: disconnected, connection-change set, power bit still reported on. No Luma queue remains active at that instant in the observed sequence.
9. After re-enumeration, tracking reconnects around06.691. A second whole-hub loss follows at **13.516230**. Tracking resumes around22.674 after another enumeration. The microphone is **not proven recovered by22seconds**: the kernel reports `unable to enumerate USB device` at21.499, with no successful microphone enumeration through11:17:29. “Tracking is back” and “every child is back” are different claims.

**What each reopen actually does**

Before the first hub loss there are **14 read-queue generations on address27**: the initial open and13 subsequent opens. Each begins with8 IMU and8 MCU-reply read URBs. That is112 initial0x85 requests across the generations, with no successful0x85 completion before hub loss. Successful IMU reads are resubmitted as streaming continues; the8+8 figures describe queue depth, not total lifetime USB traffic.

The following table lists MCU queue start times and the event ending that generation. “Silent” means the logged watchdog closed it; the EPROTO appears during that teardown.

| Queue start EST | Ending event |
|---|---|
| 11:15:23.588779 | IMU stops23.836; silent close24.840 |
| 11:15:26.839704 | 0x81 halt28.458876 |
| 11:15:30.512434 | IMU stops32.626; silent close33.628 |
| 11:15:35.627904 | 0x81 halt38.827109 |
| 11:15:40.880518 | 0x81 halt43.690726 |
| 11:15:45.743346 | 0x85 EPROTO45.762870 |
| 11:15:47.815459 | 0x85 EPROTO47.820903 |
| 11:15:49.872784 | 0x85 EPROTO50.030971 |
| 11:15:52.084048 | 0x85 EPROTO52.094990 |
| 11:15:54.146374 | 0x85 EPROTO54.153065 |
| 11:15:56.204505 | 0x85 EPROTO56.206058 |
| 11:15:58.258841 | 0x85 EPROTO58.272098 |
| 11:16:00.324802 | 0x85 EPROTO00.375182 |
| 11:16:02.427136 | 0x85 EPROTO02.488200 |

The source path opens the USBFS descriptor, claims interface0, and queues those reads. On error it discards/reaps outstanding URBs, releases interface0 and closes the descriptor. **Claims/releases are inferred from that audited path, not directly recorded by usbmon.** The trace directly corroborates cancellation and fresh read submissions. It cannot prove which PID issued an ioctl without a syscall trace.

There are **zero Luma control transfers** before the first parent loss: no observed CLEAR_HALT, SET_CONFIGURATION or control reset request. Only the one bulk start command was sent. Reconnects skip additional start commands because IMU samples arrive before the command delay. The later log's16 connections,15 disconnects,3 commands and13 skipped starts includes the physical recovery episodes. It must not be interpreted as16 start commands or15 hub removals.

**Why the two-second loop persists**

The intended transport backoff is defeated by partial recovery:

- `spike/driver/src/transport.cpp:386-393` calculates a two-second arrival hold, increasing it for failed sessions.
- `lose_link()` at468-489 increments the failure count only if that connection received no packet.
- `note_packet()` at494-509 resets the count immediately on any non-drain packet.
- Every0x85-failing generation here first receives successful IMU packets. Thus the failure history repeatedly returns to zero and the next open uses the ordinary two-second delay.

The EPIPE message says “only a re-enumeration clears that,” but there is **no same-enumeration quarantine**. The existing stall test explicitly allows retry “in case something else cleared it.” Closing/reopening does not itself send CLEAR_HALT.

On a read-side error, `TransportLink::read()` marks the link down, so the daemon's subsequent close is a no-op; it is not what erases error history in these0x85 episodes. On the separate silent-watchdog path, close really resets the transport and its history. Any patch must describe which path it changes rather than claim both are solved automatically.

**Other devices on bus1**

The Razer address9 continues approximately1,000 successful audio-IN completions/second, including through the failure sequence. In11:15:20–11:16:03.307988, there are43,307 successful iso completions and no error-status completions; largest completion gap is1.038ms. Its paired HID SET_REPORTs continue the same6.01-second cadence seen before XR starts. No new Razer burst or stream transition precedes the IMU stop.

The original glasses hub24, camera25 and microphone26 have **no recorded submissions/completions before the root-port disappearance**. That directly argues against a visible microphone/TT control storm initiating this event. usbmon does not expose every split transaction, NAK or pending hardware poll.

Logitech keyboard address3 has30 ordinary successful interrupt completions in that interval, starting11:15:32.234—after the initial IMU silence. Keyboard contents were not read or retained in the analysis. A Steam thread's split-lock trap at11:15:25.248 is also later than initial silence. Neither timing identifies an initiating cause. Shared-controller electrical/scheduling effects remain possible despite the Razer's successful completions.

The microphone's configuration/address errors happen after parent re-enumeration: `can't set config #1, error -71` around11:16:05.938, then address failures and unable-to-enumerate after the second hub loss. These are distinct from its ordinary once-per-enumeration HID descriptor-22 error.

**Comparison with clean activity and the earlier snapshots**

The captured **10:44:50 warm session start** queues the same8+8 reads, gets an already-running IMU stream, skips the start command and has no error in the inspected interval. This is useful but not a matched cold-start control: its glasses state differs.

A **clean commanded reconnect at10:39:01** queues the same reads, sends the same five-byte95 command at01.769695, receives a successful six-byte0x85 reply, and continues streaming without errors in the comparison window. Thus the read configuration and command are capable of succeeding. No retained, otherwise-identical cold-start trial proves what differed internally at11:15.

At **10:57:33**, the retained98.4-second pre-drop interval has98,413 successful IMU completions, no command and no completion errors. There is **no analogous reopen/EPROTO precursor**. At **10:57:44.885**, a0x85 EPROTO occurs only about0.65ms before the printed hub loss, during detection/teardown. The intervening reconnect had sent one95 command with a successful reply, not a multi-second failure loop. That second event also precedes the physical port swap, so its spontaneity is uncertain. The new recovery loop is therefore not a necessary explanation for all earlier drops.

**What is worth testing**

First: **bounded progressive backoff that survives brief successful packets**. The bug in the present anti-storm policy is demonstrated by source and trace, even though its role in hub loss is unproven. A fake two-endpoint test can fail under the old behavior and prove the new cooldown grows; only a later controlled glasses trial can establish whether this reduces hub failures. Preserve normal recovery for genuinely new enumeration and stop responsiveness. Expect a tradeoff: tracking takes longer to recover after transient failures.

Second, separately: **an IMU-only or on-demand0x85 experiment**. Continuous0x85 reads are implicated as the endpoint reporting later failures, but removing them is not yet a justified general fix. The first trouble is IMU silence/0x81 halt, not0x85 EPROTO; removing the reply endpoint may hide a symptom or leave replies unread. Do not combine that change with backoff in one trial.

Third: consider strict **wait-for-re-enumeration after EPIPE** if bounded backoff still churns. That makes the current halt wording truthful, but can strand recoverable sessions indefinitely and requires robust enumeration identity handling. It is a larger behavioral change than the initial experiment.

The proposed discriminator is simple: under the same workload, does the device remain enumerated while the modified driver stays quiet after failure? Continued hub loss during a long quiet period weakens the claim that rapid reopens push it over the edge. Fewer drops across repeated matched trials supports amplification, but does not identify the initial firmware/link failure.

Evidence is reproducible with [precursor.py](precursor.py); hashes, per-generation events, comparison windows and per-second headers are in [precursor-audit.json](precursor-audit.json). The previous incident report's hardware-first conclusion does not apply to this initial driver-level failure; it conflates the first tracking loss with a later parent disconnect.

**Prepared experiment, not installed**

Branch `drop-error-backoff`, commit `0b92f9d6b87e01a8735e39d55dd4700291abd9cf`, is pushed to origin. Its isolated checkout is `/home/cody/.t3/worktrees/viture-workspace/t3-6ab574b4`. Author and committer are costerou <257170158+costerou@users.noreply.github.com>. This report's branch contains analysis only; the driver patch was not merged into it or master.

The patch preserves the existing endpoints and allowed ioctls. For repeated short-lived read-error sessions, default cooldown now grows **4,8,16,32seconds**, capped there. Two seconds of sustained payload receipt clears history; gaps over100ms restart the qualifying interval. It also corrects the misleading halt message. New observed device arrivals retain the normal base settling behavior. This is a C++ transport configuration duration, not a newly exposed CLI option.

The new fake-USB regression exercises real TransportLink behavior with8+8 reads, valid IMU data followed by MCU EPROTO, and checks that no new opens, claims or submissions occur during increasing cooldowns. It failed before implementation (30 failed checks), then passed after the production change. Further cases cover the cap, idle gaps, sustained recovery, interruptibility, EPIPE, observed disappearance and the ioctl allowlist. The configured offline CTest suite passed **17/17**. The parent reviewed the production/test diff and original red/green logs, and independently reran the transport/daemon tests. Exact commands and recorded evidence are in `spike/driver/docs/drop-error-backoff-experiment.md` on that branch.

Limits: health is payload receipt, not semantic IMU validity; the silent-watchdog close and some write-close paths still reset transport history. The patch specifically addresses the repeated short read-error phase, not every recovery path. A stop/start of the driver also resets history. No live validation occurred. Slower recovery after transient faults is an expected tradeoff, and this patch does not establish or fix the initiating silence/halt by itself.
