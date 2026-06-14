# Phase 5 - Retire V2

## Goal

Remove the split runtime once the single-loop path has parity.

## Steps

- [x] Remove `WORKER_RUNTIME_VERSION` runtime branching.
- [x] Delete `app/worker_kernel/v2` source modules.
- [x] Remove the dedicated V2 test module.
- [x] Keep tests that prove the useful V2 behaviors on the single runtime.
- [x] Update docs/probe references: no app, test, script, or doc references the retired flag.

## Result

The worker runtime now has one public path again. The untracked V2 experiment
source and its dedicated test module were removed from the working tree. The
kept behavior is covered by the single-runtime worker tests:

- provider-style tool call normalization
- repairable tool-denial observations
- grouped file-operation normalization
- artifact contract repair and validation
- kernel-owned retry/replan decisions

## Verification

- Full test suite passes.
- Live probes pass or fail with clear single-loop matrix evidence.
- No runtime matrix rows reference `worker_kernel_runtime_v2`.
