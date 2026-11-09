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

// Activity-power pilot (owner 2026-11-02, plan claude-artifacts/ooc-synthesis/plans/power-pilot-plan.md):
// BoomTile identical to the OOC Baseline point (ooc.OocPointConfig variant=base,
// tools/ooc-synth/scala/src/OocPoint.scala) plus the DMI loader. WithSerialTLTiedOff/WithDMIDTM are
// harness/debug-module only. All printfs off, so +verbose prints only the harness PASSED/FAILED line.
// NOTE: names must contain NO underscore -- the Chipyard config-string parser splits on '_'.
class BaselineBoomPowerDMIConfig extends Config(
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new boom.v3.common.WithoutBoomCommitLogPrintf ++
  new boom.v3.common.WithoutCfDebugPrintf ++
  new boom.v3.common.WithoutMemtracePrintf ++
  new chipyard.config.WithNoTraceIO ++
  new freechips.rocketchip.subsystem.WithExtMemSize((1L << 30) * 16L) ++
  new freechips.rocketchip.subsystem.WithoutTLMonitors ++
  new chipyard.WithL2Wipe ++
  new BaselineBoomConfig)

// Power calibration (owner 2026-11-02 "Calibrated estimate", RULES C 'Power axis route';
// plan claude-artifacts/ooc-synthesis/plans/power-calibration-plan.md). Static sizes of one OOC Baseline Tile
// point: a copy of ooc.WithOocSizes + ooc.OocBpd (tools/ooc-synth/scala/src/OocPoint.scala:49-70, 135-179,
// width_mode=full) with the static-width issue widths of widthmap.json written out (BOOM preset of that
// decodeWidth capped at WithFuzzingBoom: width 2 -> int 2 / mem 1 / fp 1, width 1 -> 1 / 1 / 1), so the sim jar
// needs no ooc classes. spec = the point's elab.json "spec" without "variant" (a key is present only when it
// differs from the built size). Step P3 (tile_equiv.py) proves simulated tile == synthesized tile.
// If WithOocSizes, OocBpd or WithTAGELBPD change, this copy must follow.
object PowerCalSizes {
  val keys = Set("width", "rob", "fb", "ftq", "ras", "iprf", "fprf", "iq_int", "iq_mem", "iq_fp", "ldq", "stq",
    "btb_sets", "btb_ways", "tage", "ic_sets", "ic_ways", "dc_sets", "dc_ways")
  def parse(s: String): Map[String, Int] = s.split(";").map(_.trim).filter(_.nonEmpty).map { kv =>
    val i = kv.indexOf('=')
    require(i > 0 && keys.contains(kv.take(i)), s"PowerCalSizes: bad entry '$kv'")
    kv.take(i) -> kv.drop(i + 1).toInt
  }.toMap
  def issueWidth(w: Int, iqType: BigInt, built: Int): Int = w match {
    case 4 => built
    case 2 => if (iqType == boom.v3.common.IQT_INT.litValue) 2 else 1
    case 1 => 1
    case _ => throw new Exception(s"PowerCalSizes: width $w is not in coreWidthOptions (4, 2, 1)")
  }
  // instance names (val names) identical to WithTAGELBPD / ooc.OocBpd: loop, tage, btb, bim, ubtb
  def bpd(tageP: boom.v3.ifu.BoomTageParams, btbP: boom.v3.ifu.BoomBTBParams) =
    (resp_in: boom.v3.ifu.BranchPredictionBankResponse, p: org.chipsalliance.cde.config.Parameters) => {
      val loop = chisel3.Module(new boom.v3.ifu.LoopBranchPredictorBank()(p))
      val tage = chisel3.Module(new boom.v3.ifu.TageBranchPredictorBank(tageP)(p))
      val btb = chisel3.Module(new boom.v3.ifu.BTBBranchPredictorBank(btbP)(p))
      val bim = chisel3.Module(new boom.v3.ifu.BIMBranchPredictorBank()(p))
      val ubtb = chisel3.Module(new boom.v3.ifu.FAMicroBTBBranchPredictorBank()(p))
      val preds = Seq(loop, tage, btb, ubtb, bim)
      preds.map(_.io := chisel3.DontCare)
      ubtb.io.resp_in(0) := resp_in
      bim.io.resp_in(0) := ubtb.io.resp
      btb.io.resp_in(0) := bim.io.resp
      tage.io.resp_in(0) := btb.io.resp
      loop.io.resp_in(0) := tage.io.resp
      (preds, loop.io.resp)
    }
}

class WithPowerCalSizes(specStr: String) extends Config((site, here, up) => {
  case freechips.rocketchip.subsystem.TilesLocated(freechips.rocketchip.subsystem.InSubsystem) =>
    up(freechips.rocketchip.subsystem.TilesLocated(freechips.rocketchip.subsystem.InSubsystem), site) map {
      case tp: boom.v3.common.BoomTileAttachParams =>
        val spec = PowerCalSizes.parse(specStr)
        var c = tp.tileParams.core
        var ic = tp.tileParams.icache.get
        var dc = tp.tileParams.dcache.get
        spec.get("width").foreach { w =>
          c = c.copy(decodeWidth = w, issueParams = c.issueParams.map(ip =>
            ip.copy(dispatchWidth = w, issueWidth = PowerCalSizes.issueWidth(w, ip.iqType, ip.issueWidth))))
        }
        spec.get("rob").foreach(v => c = c.copy(numRobEntries = v))
        spec.get("fb").foreach(v => c = c.copy(numFetchBufferEntries = v))
        spec.get("ftq").foreach(v => c = c.copy(ftq = c.ftq.copy(nEntries = v)))
        spec.get("ras").foreach(v => c = c.copy(numRasEntries = v))
        spec.get("iprf").foreach(v => c = c.copy(numIntPhysRegisters = v))
        spec.get("fprf").foreach(v => c = c.copy(numFpPhysRegisters = v))
        def iq(key: String, t: BigInt) = spec.get(key).foreach { v =>
          c = c.copy(issueParams = c.issueParams.map(ip => if (ip.iqType == t) ip.copy(numEntries = v) else ip))
        }
        iq("iq_int", boom.v3.common.IQT_INT.litValue); iq("iq_mem", boom.v3.common.IQT_MEM.litValue)
        iq("iq_fp", boom.v3.common.IQT_FP.litValue)
        spec.get("ldq").foreach(v => c = c.copy(numLdqEntries = v))
        spec.get("stq").foreach(v => c = c.copy(numStqEntries = v))
        spec.get("ic_sets").foreach(v => ic = ic.copy(nSets = v))
        spec.get("ic_ways").foreach(v => ic = ic.copy(nWays = v))
        spec.get("dc_sets").foreach(v => dc = dc.copy(nSets = v))
        spec.get("dc_ways").foreach(v => dc = dc.copy(nWays = v))
        if (spec.contains("tage") || spec.contains("btb_sets") || spec.contains("btb_ways")) {
          val dflt = boom.v3.ifu.BoomTageParams()
          val tage = dflt.copy(tableInfo = dflt.tableInfo.take(spec.getOrElse("tage", dflt.tableInfo.size)))
          val b0 = boom.v3.ifu.BoomBTBParams()
          val sets = spec.getOrElse("btb_sets", b0.nSets)
          val btbP = b0.copy(nSets = sets, nWays = spec.getOrElse("btb_ways", b0.nWays),
            extendedNSets = b0.extendedNSets min sets) // btb.scala:35 requires extendedNSets <= nSets
          c = c.copy(branchPredictor = PowerCalSizes.bpd(tage, btbP))
        }
        tp.copy(tileParams = tp.tileParams.copy(core = c, icache = Some(ic), dcache = Some(dc)))
      case other => other
    }
})

// The five calibration / held-out tiles (Baseline). NOTE: names must contain NO underscore.
class BaselineBoomPowerCalW4DMIConfig extends Config(new BaselineBoomPowerDMIConfig)   // own sim binary; same tile as the pilot
class BaselineBoomPowerCalW2DMIConfig extends Config(new WithPowerCalSizes("width=2") ++ new BaselineBoomPowerDMIConfig)
class BaselineBoomPowerCalW1DMIConfig extends Config(new WithPowerCalSizes("width=1") ++ new BaselineBoomPowerDMIConfig)
class BaselineBoomPowerCalMin4DMIConfig extends Config(new WithPowerCalSizes(
  "btb_sets=32;btb_ways=1;dc_sets=16;dc_ways=1;fb=16;fprf=40;ftq=8;ic_sets=8;ic_ways=1;iprf=48;iq_fp=8;iq_int=8;iq_mem=8;ldq=8;ras=4;rob=32;stq=8;tage=1") ++
  new BaselineBoomPowerDMIConfig)
class BaselineBoomPowerCalHeld2DMIConfig extends Config(new WithPowerCalSizes(
  "btb_sets=64;dc_sets=64;dc_ways=4;fb=32;fprf=48;ftq=16;ic_sets=32;ic_ways=4;iprf=96;iq_fp=16;iq_int=16;iq_mem=16;ldq=24;ras=16;rob=128;stq=24;tage=5;width=2") ++
  new BaselineBoomPowerDMIConfig)
// Second held-out tile HELD3 (owner 2026-11-03 'Power calibration revision', RULES C; plan
// claude-artifacts/ooc-synthesis/plans/power-cal-revision-plan.md section 2): small-knob width-2 point, chosen and
// frozen before the v2 refit. Fuzzer cfg: coreWidth 2, rob 64, fb 24, ftq 16, ras 8, pregFileSize 64 (fprf 40),
// numIssueSlots 8, ldq 16, stq 16, btbEntries 64 (32 x 2), tage 3, I$ 16 x 2, D$ 32 x 2.
class BaselineBoomPowerCalHeld3DMIConfig extends Config(new WithPowerCalSizes(
  "btb_sets=32;dc_sets=32;dc_ways=2;fb=24;fprf=40;ftq=16;ic_sets=16;ic_ways=2;iprf=64;iq_fp=8;iq_int=8;iq_mem=8;ldq=16;ras=8;rob=64;stq=16;tage=3;width=2") ++
  new BaselineBoomPowerDMIConfig)

// Verilator IFT fuzzer config with DMI pre-execution reconfiguration support.
// Same hardware as CoreFuzzingConfig. WithDMIDTM exposes a DMI debug port so
// testchip_dtm can halt the hart, write structure-size/domain CSRs via
// +reconfig_csrs=<file>, and resume before any workload instructions execute.
// Unlike CoreFuzzingCheckpointConfig this omits the checkpoint-specific extras
// (32 GiB ext mem, WithNPMPs(0)), which are not needed for bare-metal IFT test
// runs where memory stays at the default 2 GiB range.
//
// [Task 2.10f round 2, r33 root cause] WithSerialTLTiedOff is required. Without it the
// harness also attaches SimTSI to the serial TL port, and the TSI front end loads the
// same ELF a second time over serial TL while the DMI-loaded program already runs. Its
// .bss zero fill (about 8 bytes per 205 core cycles) then overwrites data the program
// has written. See claude-artifacts/ift-overhaul/bug-reports/task-2.10f-r33.md.
// Stock dmiRocketConfig and every other DMI config in this file tie the port off too.
//
// [Task 2.10g, ruling 61] WithIFTOracle: Verilator/OOC-only IFT oracle mode. The tile keeps
// what a FireSim IFT build keeps (IFT record outputs, TracerV trace port), so the BoomTile
// OOC counts the FPGA logic. FireSim configs must not inherit this config: they list
// WithDMIDTM ++ CoreFuzzingConfig instead (BoomCoreParams also rejects oracle + bridge).
// [NOLOG 2026-10-13, owner ruling 122/123] Exit-code-only runs (ISA tests, attacks judged by exit
// code): same hardware as CoreFuzzingDMIConfig with the commit-log and CF debug printfs compiled
// out, so +verbose prints only the harness PASSED/FAILED lines. Build non-debug:
//   make -j CONFIG=CoreFuzzingDMINoLogConfig VERILATOR_THREADS=20 NUMACTL=1
class CoreFuzzingDMINoLogConfig extends Config(
  new boom.v3.common.WithoutBoomCommitLogPrintf ++
  new boom.v3.common.WithoutCfDebugPrintf ++
  new CoreFuzzingDMIConfig)

class CoreFuzzingDMIConfig extends Config(
  new boom.v3.common.WithIFTOracle ++
  new chipyard.harness.WithSerialTLTiedOff ++   // one program loader only (DMI); see above
  new chipyard.config.WithDMIDTM ++
  new CoreFuzzingConfig)

// ---- Hardware defenses (defense-clean-plans *-plan-v5.md). Every defense CSR resets to 0 = stock cycle behaviour. ----
class CoreFuzzingDMISTTConfig extends Config(new boom.v3.common.WithSTT ++ new CoreFuzzingDMIConfig)
class CoreFuzzingDMISTTNoLogConfig extends Config(new boom.v3.common.WithSTT ++ new CoreFuzzingDMINoLogConfig)
class CoreFuzzingDMISTTDoppelgangerConfig extends Config(
  new boom.v3.common.WithDoppelganger() ++ new boom.v3.common.WithSTT ++ new CoreFuzzingDMIConfig)
class CoreFuzzingDMISTTDoppelgangerNoLogConfig extends Config(
  new boom.v3.common.WithDoppelganger() ++ new boom.v3.common.WithSTT ++ new CoreFuzzingDMINoLogConfig)
class CoreFuzzingDMISpecASanConfig extends Config(new boom.v3.common.WithSpecASan() ++ new CoreFuzzingDMIConfig)
class CoreFuzzingDMISpecASanNoLogConfig extends Config(new boom.v3.common.WithSpecASan() ++ new CoreFuzzingDMINoLogConfig)
class CoreFuzzingDMICevicheConfig extends Config(new boom.v3.common.WithCeviche ++ new CoreFuzzingDMIConfig)
class CoreFuzzingDMICevicheNoLogConfig extends Config(new boom.v3.common.WithCeviche ++ new CoreFuzzingDMINoLogConfig)
class CoreFuzzingDMIAllDefConfig extends Config(
  new boom.v3.common.WithCeviche ++ new boom.v3.common.WithSpecASan() ++
  new boom.v3.common.WithDoppelganger() ++ new boom.v3.common.WithSTT ++ new CoreFuzzingDMIConfig)
class CoreFuzzingDMIAllDefNoLogConfig extends Config(
  new boom.v3.common.WithCeviche ++ new boom.v3.common.WithSpecASan() ++
  new boom.v3.common.WithDoppelganger() ++ new boom.v3.common.WithSTT ++ new CoreFuzzingDMINoLogConfig)
// No-Ceviche defense set (owner 2026-11-03, RULES C 'NoCev bitstream'): STT + Doppelganger + SpecASan, no Ceviche
// (Ceviche CRFs were the AllDef area/congestion driver). Verilator twins of FireSimCoreFuzzingIFTDMILeanNoCevConfig.
class CoreFuzzingDMINoCevConfig extends Config(
  new boom.v3.common.WithSpecASan() ++ new boom.v3.common.WithDoppelganger() ++ new boom.v3.common.WithSTT ++
  new CoreFuzzingDMIConfig)
class CoreFuzzingDMINoCevNoLogConfig extends Config(
  new boom.v3.common.WithSpecASan() ++ new boom.v3.common.WithDoppelganger() ++ new boom.v3.common.WithSTT ++
  new CoreFuzzingDMINoLogConfig)

// ---- Defense power (owner 2026-11-06 RULES C 'Power plan now, runs later'; claude-artifacts/ooc-synthesis/plans/defense-power-plan.md).
// Own names (no '_') so the traced debug build (USE_FST=1, --trace-underscore, tile_trace.vlt) never overwrites a shared simulator or
// its generated-src. Hardware = the NoLog parents (commit-log + CF debug printfs compiled out).
class CoreFuzzingDMIPowerConfig extends Config(new CoreFuzzingDMINoLogConfig)
class CoreFuzzingDMIAllDefPowerConfig extends Config(new CoreFuzzingDMIAllDefNoLogConfig)
// option C: class CoreFuzzingDMISTTPowerConfig extends Config(new CoreFuzzingDMISTTNoLogConfig)
//           class CoreFuzzingDMISTTDoppelgangerPowerConfig extends Config(new CoreFuzzingDMISTTDoppelgangerNoLogConfig)
//           class CoreFuzzingDMISpecASanPowerConfig extends Config(new CoreFuzzingDMISpecASanNoLogConfig)
//           class CoreFuzzingDMICevichePowerConfig extends Config(new CoreFuzzingDMICevicheNoLogConfig)

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

// ---------------------------------------------------------------------------------------------
// [masked-vs-static 2026-11-06] Static (stock-parameter) BOOM points with IFT on, defenses off,
// plus the masked twin under its own name. Plan: claude-artifacts/ift-overhaul/execution/task-reports/
// masked-vs-static-20261106/RUNLIST.md (sections 1, 10). Verilator debug builds only.
// No WithReconf (static), no WithSTT/WithDoppelganger/WithSpecASan/WithCeviche, no WithIFTOracle
// (fetchWidth 4 cannot carry the oracle outputs, RUNLIST P4; the oracle adds outputs only).
// The commit-log printf stays ON in every point (the tracker summary is parsed from it); Mega's
// branch printf is turned OFF (it interleaves with the commit log).
// ---------------------------------------------------------------------------------------------

// Additive: turns the branch printf off (WithBoomBranchPrintf has no inverse). Place LEFT of the core mixin.
class WithoutBoomBranchPrintf extends Config((site, here, up) => {
  case freechips.rocketchip.subsystem.TilesLocated(freechips.rocketchip.subsystem.InSubsystem) =>
    up(freechips.rocketchip.subsystem.TilesLocated(freechips.rocketchip.subsystem.InSubsystem), site) map {
      case tp: boom.v3.common.BoomTileAttachParams => tp.copy(tileParams = tp.tileParams.copy(core = tp.tileParams.core.copy(
        enableBranchPrintf = false
      )))
      case other => other
    }
})

// Mega snapped to the masked core's option values: IQ 32/32/32, issue 2/4/1, FP PRF 128/64 (int stays 128), FTQ 32.
// Place LEFT of WithNMegaBooms. issueParams order in the mixin is MEM, INT, FP.
class WithMegaExactSnap extends Config((site, here, up) => {
  case freechips.rocketchip.subsystem.TilesLocated(freechips.rocketchip.subsystem.InSubsystem) =>
    up(freechips.rocketchip.subsystem.TilesLocated(freechips.rocketchip.subsystem.InSubsystem), site) map {
      case tp: boom.v3.common.BoomTileAttachParams =>
        val c = tp.tileParams.core
        require(c.issueParams.size == 3)
        tp.copy(tileParams = tp.tileParams.copy(core = c.copy(
          issueParams = Seq(
            c.issueParams(0).copy(numEntries = 32),
            c.issueParams(1).copy(numEntries = 32),
            c.issueParams(2).copy(numEntries = 32, issueWidth = 1)),
          numFpPhysRegisters = 64,
          ftq = c.ftq.copy(nEntries = 32)
        )))
      case other => other
    }
})

// Masked twin: exactly CoreFuzzingDMIConfig's fragments, own name so its debug build lands in its own
// generated-src/simulator and never rebuilds the shared CoreFuzzingDMIConfig sim.
class MaskedTwinDMIConfig extends Config(new CoreFuzzingDMIConfig)

class SmallBoomIFTStaticDMIConfig extends Config(
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new boom.v3.common.WithoutMemtracePrintf ++
  new chipyard.WithL2Wipe ++
  new boom.v3.common.WithIFT ++
  new boom.v3.common.WithNSmallBooms(1) ++          // commit-log printf already in the mixin; 64-bit bus
  new chipyard.config.AbstractConfig)

class MediumBoomIFTStaticDMIConfig extends Config(
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new boom.v3.common.WithoutMemtracePrintf ++
  new chipyard.WithL2Wipe ++
  new boom.v3.common.WithIFT ++
  new boom.v3.common.WithBoomCommitLogPrintf ++     // Medium mixin has none; the records need it
  new boom.v3.common.WithNMediumBooms(1) ++         // 64-bit bus
  new chipyard.config.AbstractConfig)

class MegaBoomIFTStaticDMIConfig extends Config(
  new chipyard.harness.WithSerialTLTiedOff ++
  new chipyard.config.WithDMIDTM ++
  new boom.v3.common.WithoutMemtracePrintf ++
  new chipyard.WithL2Wipe ++
  new boom.v3.common.WithIFT ++
  new chipyard.WithoutBoomBranchPrintf ++
  new boom.v3.common.WithNMegaBooms(1) ++
  new chipyard.config.WithSystemBusWidth(128) ++
  new chipyard.config.AbstractConfig)

class MegaExactBoomIFTStaticDMIConfig extends Config(
  new chipyard.WithMegaExactSnap ++
  new MegaBoomIFTStaticDMIConfig)
