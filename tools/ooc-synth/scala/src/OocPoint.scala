// OOC synthesis harness, agent A: static-size point configs for the BOOM v3 corefuzzing core.
//
// One config class (ooc.OocPointConfig) is instantiated by chipyard's stage through reflection
// (--legacy-configs ooc:OocPointConfig).  Its contents come from the JVM system property
// "ooc.spec" (k=v;k=v), written by tools/ooc-synth/elaborate.sh.  Every module of a point is taken
// from a full TestHarness elaboration of that config, so constructor arguments (wakeup ports,
// read ports, TL edges, paddrBits) are exactly the ones the real tile uses.
//
// Spec keys (all optional except variant):
//   variant  base | ift | ift_nobridge | reconf_base | reconf_ift
//   width    coreWidth = decodeWidth; every issue queue's dispatchWidth follows it.  With
//            width_mode=full (default) the issue widths (hence issue ports and execution units)
//            also follow BOOM's own preset of that decodeWidth (WidthMap below); with
//            width_mode=dispatch_only they stay at the WithFuzzingBoom values (what the runtime
//            coreWidth option narrows on the reconfigurable hardware).
//   width_mode full | dispatch_only
//   rob fb ftq ras iprf fprf iq_int iq_mem iq_fp ldq stq   static entry counts
//   btb_sets btb_ways tage ic_sets ic_ways dc_sets dc_ways dc_repl
// Anything not given keeps the WithFuzzingBoom value (the hardware-built maximum).
package ooc

import org.chipsalliance.cde.config.{Config, Parameters}
import freechips.rocketchip.subsystem.{TilesLocated, InSubsystem, WithExtMemSize, WithoutTLMonitors}
import boom.v3.common._
import boom.v3.ifu._

object OocSpec {
  val variants = Set("base", "ift", "ift_nobridge", "reconf_base", "reconf_ift")
  val widthModes = Set("full", "dispatch_only")
  val sizeKeys = Seq("width", "width_mode", "rob", "fb", "ftq", "ras", "iprf", "fprf", "iq_int", "iq_mem", "iq_fp",
    "ldq", "stq", "btb_sets", "btb_ways", "tage", "ic_sets", "ic_ways", "dc_sets", "dc_ways", "dc_repl")

  def parse(s: String): Map[String, String] = {
    val m = s.split(";").map(_.trim).filter(_.nonEmpty).map { kv =>
      val i = kv.indexOf('=')
      require(i > 0, s"ooc.spec: bad entry '$kv'")
      kv.take(i) -> kv.drop(i + 1)
    }.toMap
    m.keys.foreach(k => require(k == "variant" || sizeKeys.contains(k), s"ooc.spec: unknown key '$k'"))
    require(variants.contains(m.getOrElse("variant", "")), s"ooc.spec: variant must be one of $variants")
    require(widthModes.contains(m.getOrElse("width_mode", "full")), s"ooc.spec: width_mode must be one of $widthModes")
    m
  }

  def fromProps: Map[String, String] = parse(
    Option(System.getProperty("ooc.spec")).getOrElse(throw new Exception("ooc.spec system property is not set")))
}

// The TAGE-L predictor chain of WithTAGELBPD (config-mixins.scala), with explicit TAGE and BTB
// parameters.  Used ONLY when the point overrides tage/btb_*; at max size the tree's own
// WithTAGELBPD function is kept untouched.  If WithTAGELBPD's chain changes, this must follow.
object OocBpd {
  // val names = instance names, identical to WithTAGELBPD's (loop, tage, btb, bim, ubtb)
  def chain(tageP: BoomTageParams, btbP: BoomBTBParams) =
    (resp_in: BranchPredictionBankResponse, p: Parameters) => {
      val loop = chisel3.Module(new LoopBranchPredictorBank()(p))
      val tage = chisel3.Module(new TageBranchPredictorBank(tageP)(p))
      val btb = chisel3.Module(new BTBBranchPredictorBank(btbP)(p))
      val bim = chisel3.Module(new BIMBranchPredictorBank()(p))
      val ubtb = chisel3.Module(new FAMicroBTBBranchPredictorBank()(p))
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

// Issue widths of a static width-w core (owner decision 2026-10-28): BOOM's own preset whose
// decodeWidth is w (config-mixins.scala WithNSmall/Medium/Large/MegaBooms), each issue width capped
// at the WithFuzzingBoom value (the hardware-built maximum; e.g. FP stays 1).  At the fuzzing core's
// own decodeWidth the fuzzing issueParams are kept unchanged.  The presets are evaluated, not copied.
object WidthMap {
  private def coreOf(c: Config): BoomCoreParams = {
    val p: Parameters = new Config(c ++ new chipyard.config.AbstractConfig)
    val cores = p(TilesLocated(InSubsystem)).collect { case b: BoomTileAttachParams => b.tileParams.core }
    require(cores.size == 1, s"WidthMap: expected one BOOM tile, got ${cores.size}")
    cores.head
  }
  lazy val fuzzing: BoomCoreParams = coreOf(new WithFuzzingBoom(1))
  lazy val presets: Seq[(String, BoomCoreParams)] = Seq(
    "WithNSmallBooms" -> (() => new WithNSmallBooms(1)),
    "WithNMediumBooms" -> (() => new WithNMediumBooms(1)),
    "WithNLargeBooms" -> (() => new WithNLargeBooms(1)),
    "WithNMegaBooms" -> (() => new WithNMegaBooms(1))).map { case (n, f) => n -> coreOf(f()) }
  val iqNames = Seq(IQT_INT.litValue -> "int", IQT_MEM.litValue -> "mem", IQT_FP.litValue -> "fp")

  /** (preset name, iqType -> issueWidth) for a static width-w core built from `fz` */
  def issueWidths(fz: BoomCoreParams, w: Int): (String, Map[BigInt, Int]) = {
    if (w == fz.decodeWidth) ("WithFuzzingBoom", fz.issueParams.map(ip => ip.iqType -> ip.issueWidth).toMap)
    else {
      val cand = presets.filter(_._2.decodeWidth == w)
      require(cand.size == 1, s"WidthMap: ${cand.size} BOOM presets have decodeWidth $w (${presets.map(x => x._1 + "=" + x._2.decodeWidth)})")
      val (n, pc) = cand.head
      (n, fz.issueParams.map { ip =>
        val pw = pc.issueParams.find(_.iqType == ip.iqType)
          .getOrElse(throw new Exception(s"WidthMap: $n has no issue queue of type ${ip.iqType}")).issueWidth
        ip.iqType -> (pw min ip.issueWidth)
      }.toMap)
    }
  }
}

// Prints the width -> issue width mapping for every coreWidthOptions value as JSON.
object DumpWidthMap {
  def main(args: Array[String]): Unit = {
    val ws = (new freechips.rocketchip.util.CoreFuzzingConstants {}).coreWidthOptions
    val fz = WidthMap.fuzzing
    val rows = ws.map { w =>
      val (n, m) = WidthMap.issueWidths(fz, w)
      val pres = WidthMap.presets.find(_._2.decodeWidth == w).map(_._2)
      val iw = WidthMap.iqNames.map { case (t, nm) => "\"" + nm + "\": " + m(t) }.mkString(", ")
      val pw = pres.map(pc => WidthMap.iqNames.map { case (t, nm) =>
        "\"" + nm + "\": " + pc.issueParams.find(_.iqType == t).get.issueWidth }.mkString("{", ", ", "}")).getOrElse("null")
      "  \"" + w + "\": {\"source\": \"" + n + "\", \"issue_width\": {" + iw + "}, \"preset_issue_width\": " + pw + "}"
    }
    val fzs = WidthMap.iqNames.map { case (t, nm) => "\"" + nm + "\": " + fz.issueParams.find(_.iqType == t).get.issueWidth }.mkString("{", ", ", "}")
    val js = (rows :+ ("  \"fuzzing_issue_width\": " + fzs)).mkString("{\n", ",\n", "\n}\n")
    if (args.nonEmpty) { val wr = new java.io.PrintWriter(args(0)); wr.write(js); wr.close() } else print(js)
  }
}

class WithOocSizes(spec: Map[String, String]) extends Config((site, here, up) => {
  case TilesLocated(InSubsystem) => up(TilesLocated(InSubsystem), site) map {
    case tp: BoomTileAttachParams =>
      def g(k: String): Option[Int] = spec.get(k).map(_.toInt)
      var c = tp.tileParams.core
      var ic = tp.tileParams.icache.get
      var dc = tp.tileParams.dcache.get
      g("width").foreach { w =>
        val full = spec.getOrElse("width_mode", "full") == "full"
        val iw = WidthMap.issueWidths(c, w)._2
        c = c.copy(decodeWidth = w, issueParams = c.issueParams.map(ip =>
          ip.copy(dispatchWidth = w, issueWidth = if (full) iw(ip.iqType) else ip.issueWidth)))
      }
      g("rob").foreach(v => c = c.copy(numRobEntries = v))
      g("fb").foreach(v => c = c.copy(numFetchBufferEntries = v))
      g("ftq").foreach(v => c = c.copy(ftq = c.ftq.copy(nEntries = v)))
      g("ras").foreach(v => c = c.copy(numRasEntries = v))
      g("iprf").foreach(v => c = c.copy(numIntPhysRegisters = v))
      g("fprf").foreach(v => c = c.copy(numFpPhysRegisters = v))
      def iq(key: String, t: BigInt) = g(key).foreach { v =>
        c = c.copy(issueParams = c.issueParams.map(ip => if (ip.iqType == t) ip.copy(numEntries = v) else ip))
      }
      iq("iq_int", IQT_INT.litValue); iq("iq_mem", IQT_MEM.litValue); iq("iq_fp", IQT_FP.litValue)
      g("ldq").foreach(v => c = c.copy(numLdqEntries = v))
      g("stq").foreach(v => c = c.copy(numStqEntries = v))
      g("dc_repl").foreach(v => c = c.copy(dcacheReplPolicy = v))
      g("ic_sets").foreach(v => ic = ic.copy(nSets = v))
      g("ic_ways").foreach(v => ic = ic.copy(nWays = v))
      g("dc_sets").foreach(v => dc = dc.copy(nSets = v))
      g("dc_ways").foreach(v => dc = dc.copy(nWays = v))
      if (spec.contains("tage") || spec.contains("btb_sets") || spec.contains("btb_ways")) {
        val dflt = BoomTageParams()
        // tables=v keeps tableInfo.take(v): the same tables the runtime option v enables
        // (i < cf_tage_active, tage.scala:343-349); table 3 stays the GShare bank when v > 3.
        val tage = dflt.copy(tableInfo = dflt.tableInfo.take(g("tage").getOrElse(dflt.tableInfo.size)))
        val b0 = BoomBTBParams()
        val sets = g("btb_sets").getOrElse(b0.nSets)
        val btbP = b0.copy(nSets = sets, nWays = g("btb_ways").getOrElse(b0.nWays),
          extendedNSets = b0.extendedNSets min sets) // btb.scala:35 requires extendedNSets <= nSets
        c = c.copy(branchPredictor = OocBpd.chain(tage, btbP))
      }
      tp.copy(tileParams = tp.tileParams.copy(core = c, icache = Some(ic), dcache = Some(dc)))
    case other => other
  }
})

object OocPoint {
  // Variant gates, mirroring the FireSim lean area configs (firechip TargetConfigs.scala:337-381):
  //   base        neither gate                       (FireSimBaselineBoomDMILeanConfig)
  //   reconf_base WithReconf                         (FireSimReconfBoomDMILeanConfig)
  //   ift         WithIFT + WithIFTBridge            (the IFT core of FireSimCoreFuzzingIFTDMILeanConfig, reconf off)
  //   reconf_ift  WithIFT + WithReconf + WithIFTBridge (FireSimCoreFuzzingIFTDMILeanConfig)
  //   ift_nobridge WithIFT only (no state taps/record outputs); not a FireSim build, see agentA.md
  def gates(variant: String): Config = {
    val ift = variant == "ift" || variant == "reconf_ift" || variant == "ift_nobridge"
    val bridge = variant == "ift" || variant == "reconf_ift"
    val reconf = variant.startsWith("reconf")
    val fr = Seq(
      if (bridge) Some(new WithIFTBridge) else None,
      if (ift) Some(new WithIFT) else None,
      if (reconf) Some(new WithReconf) else None).flatten
    fr.foldRight(new Config(Parameters.empty))((a, b) => new Config(a ++ b))
  }

  def config(spec: Map[String, String]): Config = new Config(
    new WithOocSizes(spec) ++
    gates(spec("variant")) ++
    // printf / trace gates of the FireSim lean configs (no simulation-only logic, no TracerV)
    new WithoutBoomCommitLogPrintf ++
    new WithoutCfDebugPrintf ++
    new WithoutMemtracePrintf ++
    new chipyard.config.WithNoTraceIO ++
    // 16 GiB target DRAM as in WithFireSimDesignTweaks: sets the sbus address width, hence paddrBits
    new WithExtMemSize((1L << 30) * 16L) ++
    // TL monitors are assertion-only (removed by synthesis); dropping them only speeds elaboration
    new WithoutTLMonitors ++
    new chipyard.WithL2Wipe ++
    new WithFuzzingBoom(1) ++
    new chipyard.config.WithSystemBusWidth(128) ++
    new chipyard.config.AbstractConfig)
}

class OocPointConfig extends Config(OocPoint.config(OocSpec.fromProps))

// Writes every CoreFuzzingConstants *Options list as one JSON object {name: [ints]}.
// The list names are discovered by reflection, so nothing is copied by hand.
object DumpOptions {
  def main(args: Array[String]): Unit = {
    val obj = new freechips.rocketchip.util.CoreFuzzingConstants {}
    val ms = obj.getClass.getMethods.filter(m => m.getName.endsWith("Options") && m.getParameterCount == 0)
      .sortBy(_.getName)
    val entries = ms.flatMap { m =>
      m.invoke(obj) match {
        case s: Seq[_] if s.forall(_.isInstanceOf[Int]) => Some("  \"" + m.getName + "\": [" + s.mkString(", ") + "]")
        case _ => None
      }
    }
    val js = entries.mkString("{\n", ",\n", "\n}\n")
    if (args.nonEmpty) { val w = new java.io.PrintWriter(args(0)); w.write(js); w.close() } else print(js)
  }
}
