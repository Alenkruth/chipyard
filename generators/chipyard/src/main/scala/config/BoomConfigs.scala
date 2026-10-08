package chipyard

import org.chipsalliance.cde.config.{Config}

// --------------------
// Boom V3 CoreFuzzing Configs
// Do not use these for non-corefuzzing applications
// --------------------
// 
// class SmallBoomConfig extends Config(
//   new boom.common.WithNSmallBooms(1) ++                          // small boom config
//   new chipyard.config.AbstractConfig)
// 
// class MediumBoomConfig extends Config(
//   new boom.common.WithNMediumBooms(1) ++                         // medium boom config
//   new chipyard.config.AbstractConfig)
// 
// class LargeBoomConfig extends Config(
//   new boom.common.WithNLargeBooms(1) ++                          // large boom config
//   new chipyard.config.WithSystemBusWidth(128) ++
//   new chipyard.config.AbstractConfig)
// 
// class MegaBoomConfig extends Config(
//   new boom.common.WithNMegaBooms(1) ++                           // mega boom config
//   new chipyard.config.WithSystemBusWidth(128) ++
//   new chipyard.config.AbstractConfig)
// 
// class GigaBoomConfig extends Config(
//   new boom.common.WithNGigaBooms(1) ++                           // giga boom config
//   new chipyard.config.WithSystemBusWidth(128) ++
//   new chipyard.config.AbstractConfig
//   )
// 
// // core fuzzing boom config
// class GigaBoomConfigCF extends Config(
//   new boom.common.WithNCFGigaBooms(1) ++                           // giga boom config
//   new chipyard.config.WithSystemBusWidth(128) ++
//   new chipyard.config.AbstractConfig ++
//   new boom.common.WithBoomCommitLogPrintf
//   )
// 
// ---------------------


// ---------------------
// BOOM V3 Configs
// Performant, stable baseline
// ---------------------

class SmallBoomV3Config extends Config(
  new boom.v3.common.WithNSmallBooms(1) ++                          // small boom config
  new chipyard.config.AbstractConfig)

class MediumBoomV3Config extends Config(
  new boom.v3.common.WithNMediumBooms(1) ++                         // medium boom config
  new chipyard.config.AbstractConfig)

class LargeBoomV3Config extends Config(
  new boom.v3.common.WithNLargeBooms(1) ++                          // large boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class MegaBoomV3Config extends Config(
  new boom.v3.common.WithNMegaBooms(1) ++                           // mega boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class GigaBoomV3Config extends Config(
  new boom.v3.common.WithNGigaBooms(1) ++ // Giga Boom Config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class TeraBoomV3Config extends Config(
  new boom.v3.common.WithNTeraBooms(1) ++ // Tera Boom Config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// [reconf-fix Phase C] RESTORE-ONLY L2 directory-wipe register at L2ctrl+0x100.
// Applied to CoreFuzzingConfig ONLY — BaselineBoomConfig and the other
// variants keep the stock L2 (netlist unchanged).
class WithL2Wipe extends Config((site, here, up) => {
  case freechips.rocketchip.subsystem.InclusiveCacheKey =>
    up(freechips.rocketchip.subsystem.InclusiveCacheKey).copy(enableWipe = true)
})

class CoreFuzzingConfig extends Config(
  // [NOMEMTRACE 2026-09-08] *** CONFIG CHANGE — flagged, one line to revert ***
  // The LSU memory-access trace ("MT ..." lines) is a SEPARATE gate from the CF-debug
  // printfs and defaults to ON, so every IFT run carried it.  It is:
  //   - consumed by NOTHING: every reference to "MT" in parse_ift_log.py is clobber-
  //     HANDLING (skipping interleaved MT text), not consumption;
  //   - actively destructive: the MT printf interleaves with the commit printf and
  //     replaces the whole CF(...) payload, leaving `3 0x<pc> MT ...`.  MEASURED on the
  //     last t31: 6,412 of 37,727 records (17%) clobbered; on one spectre run 12.2% of
  //     lines.  It produced FIVE false results in one session -- a fake 27% regression on
  //     spectre's bounds branch, two "NEVER COMMITTED" verdicts, and a false INCONCLUSIVE.
  // Scala `if`, so no synthesis or area impact -- it only stops emitting the lines.
  // Revert by deleting this one line if a memory trace is ever needed.
  new boom.v3.common.WithoutMemtracePrintf ++
  new chipyard.WithL2Wipe ++               // [reconf-fix Phase C] L2 wipe for checkpoint restore
  new boom.v3.common.WithIFT ++            // compile-time gate: enable DIFT tracking
  new boom.v3.common.WithReconf ++         // compile-time gate: enable runtime reconfigurability
  new boom.v3.common.WithFuzzingBoom(1) ++ // Core Fuzzing Boom Config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// CFDBG variant: CoreFuzzingConfig with the per-instruction commit-log printf turned OFF
// (WithoutBoomCommitLogPrintf overrides the flag WithFuzzingBoom sets). A `make debug`
// build of this with +verbose then emits ONLY the dcache [CFDBG-*] printfs — no commit-log
// flood, so the load isn't slow. Debug-only; identical RTL behavior otherwise.
class CoreFuzzingCFDBGConfig extends Config(
  new boom.v3.common.WithoutBoomCommitLogPrintf ++
  new CoreFuzzingConfig)

// BaselineBoomConfig — same BOOM hardware sizing as CoreFuzzingConfig but with
// both compile-time feature gates OFF (enableIFT=false, enableReconf=false).
// Builds a plain 4-wide BOOM v3 with no DIFT tracking logic and no runtime
// reconfigurability infrastructure.  Intended as the area/LUT baseline against
// which the other three variants (IFTOnlyBoomConfig, ReconfOnlyBoomConfig,
// CoreFuzzingConfig) are compared.
class BaselineBoomConfig extends Config(
  new boom.v3.common.WithFuzzingBoom(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// IFTOnlyBoomConfig — DIFT tracking enabled, runtime reconfigurability disabled.
// Same BOOM hardware sizing as CoreFuzzingConfig but the quiesce FSM, sizing
// CSRs, and anti-aliasing SRAMs are all elided.  IFT runs continuously with
// no pipeline-drain mechanism between experiments.
class IFTOnlyBoomConfig extends Config(
  new boom.v3.common.WithIFT ++
  new boom.v3.common.WithFuzzingBoom(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// ReconfOnlyBoomConfig — runtime reconfigurability enabled, DIFT tracking
// disabled.  Quiesce FSM, sizing CSRs, and (where enabled in Step 7) full_idx_snap
// SRAMs are present; all IFT state and influencer tracking is elided.
class ReconfOnlyBoomConfig extends Config(
  new boom.v3.common.WithReconf ++
  new boom.v3.common.WithFuzzingBoom(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// Checkpoint-restore variant of CoreFuzzingConfig.
// Layers the same three fragments used by dmiCheckpointingMediumBoom{V3,V4}Config
// so that `make run-binary LOADARCH=<dir>` can restore spike-generated checkpoints
// into a Verilator/VCS simulation of the BOOM fuzzing target:
//   - WithNPMPs(0)           drop PMPs so there's no non-core state to restore
//   - WithSerialTLTiedOff    disable SerialTL (not used by the DMI restore path)
//   - WithDMIDTM             expose a clocked DMI debug port that the
//                            testchip_dtm driver uses to restore arch state
//   - WithExtMemSize(16 GiB) matches spike -m0x80000000:0x400000000 used for
//                            checkpoint generation. The spike -m size MUST match
//                            the target ExtMem size or high-address segments fall
//                            outside FASED range and the simulation crashes.
//                            16 GiB is the single-host-channel default; the Alveo
//                            U250 cl_firesim shell can now be built with 2 DDR4
//                            channels (HostMemNumChannels=2 in XilinxAlveoU250Config)
//                            for 32 GiB — use the FireSim*Checkpoint32GBConfig
//                            target configs and spike -m0x80000000:0x800000000.
class CoreFuzzingCheckpointConfig extends Config(
  new freechips.rocketchip.subsystem.WithExtMemSize((BigInt(16) << 30)) ++
  new chipyard.config.WithNPMPs(0) ++
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new CoreFuzzingConfig)

// Checkpoint-restore variant of IFTOnlyBoomConfig (IFT enabled, no Reconf).
// Use with FireSimIFTOnlyBoomCheckpointConfig for SimPoint IPC overhead measurement.
class IFTOnlyBoomCheckpointConfig extends Config(
  new freechips.rocketchip.subsystem.WithExtMemSize((BigInt(16) << 30)) ++
  new chipyard.config.WithNPMPs(0) ++
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new IFTOnlyBoomConfig)

// Checkpoint-restore variant of ReconfOnlyBoomConfig (Reconf enabled, no IFT).
// Use with FireSimReconfBoomCheckpointConfig for SimPoint IPC overhead measurement.
class ReconfBoomCheckpointConfig extends Config(
  new chipyard.WithL2Wipe ++               // [reconf-fix Phase C] restore-flow L2 wipe register
  new freechips.rocketchip.subsystem.WithExtMemSize((BigInt(16) << 30)) ++
  new chipyard.config.WithNPMPs(0) ++
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new ReconfOnlyBoomConfig)

// Chipyard Verilator (sims/verilator) checkpoint-restore config, for commit-log lockstep
// vs spike. NOTE: name has NO underscore -- the Chipyard config-string parser splits on '_'.
//   WithExtMemSize(32 GiB) overrides the 16 GiB above (leftmost wins) for the checkpoint's
//   PT_LOAD range; WithSV57 for the Sv57 satp; commit log is already ON via
//   ReconfOnlyBoomConfig -> WithFuzzingBoom (no WithoutBoomCommitLogPrintf here). WithDMIDTM
//   -> TestchipSimDTM drives +loadarch; SimDRAM parses +loadmem host-side.
// Commit-log variant: identical hardware to ReconfBoomCheckpointSv57Config, but ALL
// corefuzzing debug printfs are compile-time removed so the commit log emits only the
// spike-standard fields (priv, pc, inst, rd, wdata) and can be diffed byte-for-byte
// against a spike commit log. Separate config -- the default corefuzzing behavior
// (enableCfDebugPrintf = true) is untouched.
class ReconfBoomCheckpointCommitLogConfig extends Config(
  new boom.v3.common.WithoutCfDebugPrintf ++
  new boom.v3.common.WithoutMemtracePrintf ++     // also kill the LSU "MT ..." memtrace dump
  new ReconfBoomCheckpointSv57Config)

class ReconfBoomCheckpointSv57Config extends Config(
  new freechips.rocketchip.subsystem.WithExtMemSize((BigInt(32) << 30)) ++
  new chipyard.config.WithSV57 ++
  new ReconfBoomCheckpointConfig)

// Checkpoint-restore variant of BaselineBoomConfig (no IFT, no Reconf).
// Same WithFuzzingBoom microarch as CoreFuzzingConfig but plain 4-wide BOOM.
// Use with FireSimBaselineBoomCheckpointConfig for FPGA SimPoint IPC measurement.
// Checkpoints MUST be generated with spike -m matching the target ExtMem size:
// -m0x80000000:0x400000000 (16 GiB, single host channel) or, with the 2-channel
// U250 build, -m0x80000000:0x800000000 (32 GiB) via FireSimBaselineBoomCheckpoint32GBConfig.
class BaselineBoomCheckpointConfig extends Config(
  new freechips.rocketchip.subsystem.WithExtMemSize((BigInt(16) << 30)) ++
  new chipyard.config.WithNPMPs(0) ++
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new BaselineBoomConfig)

// Controlled differential against ReconfBoomCheckpoint{Sv57,CommitLog}Config: IDENTICAL
// layering (32 GiB, Sv57, NPMPs(0), SerialTLTiedOff, DMIDTM, gated debug printfs) but on
// BaselineBoomConfig, i.e. enableIFT=false AND enableReconf=false. Used to determine
// whether the illegal committed branch redirects observed on the reconf core come from
// the runtime-reconfigurability logic or from the always-on corefuzzing changes.
// NOTE: names must contain NO underscore -- the Chipyard config-string parser splits on '_'.
class BaselineBoomCheckpointCommitLogConfig extends Config(
  new boom.v3.common.WithoutCfDebugPrintf ++
  new boom.v3.common.WithoutMemtracePrintf ++  // [2026-08-07] was missing vs the Reconf CommitLog
      // config ("identical layering" was the stated intent): without it the LSU MT printfs run in
      // a different mtask and, under multithreaded Verilator, splice INTO the multi-statement
      // commit printf — garbling ~half the commit lines and making check_cf/spike diffs unusable.
  new BaselineBoomCheckpointSv57Config)

class BaselineBoomCheckpointSv57Config extends Config(
  new freechips.rocketchip.subsystem.WithExtMemSize((BigInt(32) << 30)) ++
  new chipyard.config.WithSV57 ++
  new BaselineBoomCheckpointConfig)

// Verilator IFT fuzzer config with DMI pre-execution reconfiguration support.
// Same hardware as CoreFuzzingConfig. WithDMIDTM exposes a DMI debug port so
// testchip_dtm can halt the hart, write structure-size/domain CSRs via
// +reconfig_csrs=<file>, and resume before any workload instructions execute.
// Unlike CoreFuzzingCheckpointConfig this omits the checkpoint-specific extras
// (32 GiB ext mem, WithNPMPs(0), WithSerialTLTiedOff) — not needed for
// bare-metal IFT test runs where memory stays at the default 2 GiB range.
class CoreFuzzingDMIConfig extends Config(
  new chipyard.config.WithDMIDTM ++
  new CoreFuzzingConfig)

// FireSim FPGA variant: inherits ALL parameters from CoreFuzzingConfig.
// Adds WithIFTBridge which exports IFT commit/squash records as tile IO for GoldenGate synthesis.
// WithIFTPunchthrough (in AbstractConfig) creates the IFTPort; WithIFTVBridge (firechip) connects it.
// To change any hardware parameters, edit only CoreFuzzingConfig above.
class CoreFuzzingFireSimConfig extends Config(
  new boom.v3.common.WithoutBoomCommitLogPrintf ++ // IFTBridge replaces printfs; suppress PrintBridge synthesis
  new boom.v3.common.WithIFTBridge ++
  new CoreFuzzingConfig)

class DualSmallBoomV3Config extends Config(
  new boom.v3.common.WithNSmallBooms(2) ++                          // 2 boom cores
  new chipyard.config.AbstractConfig)

class Cloned64MegaBoomV3Config extends Config(
  new boom.v3.common.WithCloneBoomTiles(63, 0) ++
  new boom.v3.common.WithNMegaBooms(1) ++                           // mega boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class LoopbackNICLargeBoomV3Config extends Config(
  new chipyard.harness.WithLoopbackNIC ++                        // drive NIC IOs with loopback
  new icenet.WithIceNIC ++                                       // build a NIC
  new boom.v3.common.WithNLargeBooms(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class MediumBoomV3CosimConfig extends Config(
  new chipyard.harness.WithCospike ++                            // attach spike-cosim
  new chipyard.config.WithTraceIO ++                             // enable the traceio
  new boom.v3.common.WithNMediumBooms(1) ++
  new chipyard.config.AbstractConfig)

class dmiCheckpointingMediumBoomV3Config extends Config(
  new chipyard.config.WithNPMPs(0) ++                            // remove PMPs (reduce non-core arch state)
  new chipyard.harness.WithSerialTLTiedOff ++                    // don't attach anything to serial-tl
  new chipyard.config.WithDMIDTM ++                              // have debug module expose a clocked DMI port
  new boom.v3.common.WithNMediumBooms(1) ++
  new chipyard.config.AbstractConfig)

class dmiMediumBoomV3CosimConfig extends Config(
  new chipyard.harness.WithCospike ++                            // attach spike-cosim
  new chipyard.config.WithTraceIO ++                             // enable the traceio
  new chipyard.harness.WithSerialTLTiedOff ++                    // don't attach anythint to serial-tl
  new chipyard.config.WithDMIDTM ++                              // have debug module expose a clocked DMI port
  new boom.v3.common.WithNMediumBooms(1) ++
  new chipyard.config.AbstractConfig)

class SimBlockDeviceMegaBoomV3Config extends Config(
  new chipyard.harness.WithSimBlockDevice ++                     // drive block-device IOs with SimBlockDevice
  new testchipip.iceblk.WithBlockDevice ++                       // add block-device module to peripherybus
  new boom.v3.common.WithNMegaBooms(1) ++                        // mega boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// ---------------------
// BOOM V4 Configs
// Less stable and performant, but with more advanced micro-architecture
// Use for PD exploration
// ---------------------

class SmallBoomV4Config extends Config(
  new boom.v4.common.WithNSmallBooms(1) ++                          // small boom config
  new chipyard.config.AbstractConfig)

class MediumBoomV4Config extends Config(
  new boom.v4.common.WithNMediumBooms(1) ++                         // medium boom config
  new chipyard.config.AbstractConfig)

class LargeBoomV4Config extends Config(
  new boom.v4.common.WithNLargeBooms(1) ++                          // large boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class MegaBoomV4Config extends Config(
  new boom.v4.common.WithNMegaBooms(1) ++                           // mega boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class DualSmallBoomV4Config extends Config(
  new boom.v4.common.WithNSmallBooms(2) ++                          // 2 boom cores
  new chipyard.config.AbstractConfig)

class Cloned64MegaBoomV4Config extends Config(
  new boom.v4.common.WithCloneBoomTiles(63, 0) ++
  new boom.v4.common.WithNMegaBooms(1) ++                           // mega boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class MediumBoomV4CosimConfig extends Config(
  new chipyard.harness.WithCospike ++                            // attach spike-cosim
  new chipyard.config.WithTraceIO ++                             // enable the traceio
  new boom.v4.common.WithNMediumBooms(1) ++
  new chipyard.config.AbstractConfig)

class dmiCheckpointingMediumBoomV4Config extends Config(
  new chipyard.config.WithNPMPs(0) ++                            // remove PMPs (reduce non-core arch state)
  new chipyard.harness.WithSerialTLTiedOff ++                    // don't attach anything to serial-tl
  new chipyard.config.WithDMIDTM ++                              // have debug module expose a clocked DMI port
  new boom.v4.common.WithNMediumBooms(1) ++
  new chipyard.config.AbstractConfig)

class dmiMediumBoomV4CosimConfig extends Config(
  new chipyard.harness.WithCospike ++                            // attach spike-cosim
  new chipyard.config.WithTraceIO ++                             // enable the traceio
  new chipyard.harness.WithSerialTLTiedOff ++                    // don't attach anythint to serial-tl
  new chipyard.config.WithDMIDTM ++                              // have debug module expose a clocked DMI port
  new boom.v4.common.WithNMediumBooms(1) ++
  new chipyard.config.AbstractConfig)

class SimBlockDeviceMegaBoomV4Config extends Config(
  new chipyard.harness.WithSimBlockDevice ++                     // drive block-device IOs with SimBlockDevice
  new testchipip.iceblk.WithBlockDevice ++                       // add block-device module to peripherybus
  new boom.v4.common.WithNMegaBooms(1) ++                        // mega boom config
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

// [2026-08-07 illegal-CF differential] Stock BOOM v3 (WithNMegaBooms 4-wide — none of
// WithFuzzingBoom's core modifications) with the same checkpoint-restore + commit-log
// layering.  Third leg of the differential for the committed-wrong-path-redirect bug
// (observed on both the Reconf and Baseline corefuzzing flavors at mcf 0x139xx):
//   present here too  -> tree-wide/unconditional changes or upstream BOOM v3;
//   absent here       -> WithFuzzingBoom's always-on core changes (note: single sample,
//                        different D$ sizing changes timing, so absence is weak evidence).
class StockBoomV3CheckpointCommitLogConfig extends Config(
  new boom.v3.common.WithBoomCommitLogPrintf ++
  new boom.v3.common.WithoutCfDebugPrintf ++
  new boom.v3.common.WithoutMemtracePrintf ++
  new freechips.rocketchip.subsystem.WithExtMemSize((BigInt(32) << 30)) ++
  new chipyard.config.WithSV57 ++
  new chipyard.config.WithNPMPs(0) ++
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new boom.v3.common.WithNMegaBooms(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)
