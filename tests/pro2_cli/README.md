# Genuine packaged CLI acceptance (no hardware)

`PRO2_QA_CLI_BUNDLE=/absolute/fresh/package python tests/pro2_cli/run.py`

Run establishes private user+IPC+network namespaces, fake runtime/home/config/state,
fake Wayland/DISPLAY, nonexistent private D-Bus, notify-send stub. Copied Python
executable namedHyprland hosts private realUnixRPC/event/Wayland endpoints so real
HyprlandClient constructor/SO_PEERCRED/exe/lock/session/transport checks execute.
Wire model responds to expected physicalmonitor/plugin/broker/eval/bind queries;
unknown requests are failures. Brokercreate asserts realjournal pending intent.

The actual shipped shell entry is executed as subprocess. A python3PATHshim
installs only synthetic SDK admission/librarypins/fixturearguments and device/
CDLL/socket audit hooks before executing its exact original-c string. No
execute/run/clientfactory/managed/journal/viewcontrol/_launch substitution. No
Python source overlays: all packagedsource and original entry stay intact.
Onlyexternal artifacts are substituted and repinned in a disposable TESTbundle:
PRO2_SYNTHETICworker + CSDKfixture and CPUrendererendpoint. Productionbundle is
independentlyaudited and contains none of those replacements.

Real native SDKworker→Publisher feeds the CPUchild's real PoseFeed/PoseStateprobe.
The CPUchild validates start/capture/privateprojection/FD arguments and sends
heartbeats. Generated binds, real outputtransaction/recovery and actual spawned
guard RPCs execute. It does NOT execute a compositor,Lua/GPUrenderer or create a
physical picture. Direct loopbackwire simulation does not validate Hyprland
semantics,driver graphics or real SDK/hardware; those remain the parent gate.

Cases: success; rendererexit9 truthful failure/cleanup; partialbrokercreatefailure
retainsauthority then actual secondCLI --recover unloads withoutSDKcall; SDKopen
failurebeforeplugin. All assert results,journalops,APIteardown,noIPCleaks. Success
requires brokerinspect from a real separate guardprocess PID. Faults are external
endpointscenarios, not shortcuts replacing runtime orchestration.

Beforefix the same subprocess reproduced explicit Pro2 supervisedtestscoperequired
with cleanSDK/plugin teardown: runpy __main__.TestScope differed from canonical
managedimport. Scope/client types now live only in canonical scope.py; strictgate
remains. Shell imports canonicalmain directly. This harness must be run against a
fresh package and a finalarchive extraction; importedexecute tests alone are not
fullentrypoint acceptance.
