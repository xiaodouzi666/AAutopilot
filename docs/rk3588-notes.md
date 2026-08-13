# RK3588 portability notes

RK3588/RK3588S is an optional portability target, not the primary Cloud AI benchmark. After
the main Arm64 Linux evidence passes, run the doctor and smoke workflow on the board and compare
the allowed performance-cluster affinity set with all allowed cores. Keep its system manifest
and results separate; never calculate a speedup between the board and the cloud target.
